from __future__ import annotations

import json
import math
import secrets
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import catalogs, db, engine, universal
from .auth import expiry_iso, hash_password, new_session_token, token_hash, verify_password
from .config import ALLOW_REGISTRATION, AUDIO_EXTENSIONS, MUSIC_DIR, SESSION_DAYS, USER_DATA_DIR
from .media import cache_remote_audio, clear_suno_cookies, clear_suno_session, ffprobe, get_suno_connector_key, read_tags, refresh_suno_session, resolve_suno_url, rotate_suno_connector_key, save_suno_browser_session, save_suno_cookies, save_suno_session, suno_auth_status as media_suno_auth_status, suno_cookie_status, valid_local_audio
from .sources import browse_source, download_source_audio, source_folder_label, test_source
from .storage import ensure_user_storage, migrate_legacy_shared_music, repair_user_storage, user_music_root

BASE_DIR=Path(__file__).resolve().parent
app=FastAPI(title='Tasia Streamer',version='2.0.0-beta29')
app.mount('/static',StaticFiles(directory=BASE_DIR/'static'),name='static')

@app.middleware('http')
async def disable_ui_cache(request:Request, call_next):
    response=await call_next(request)
    path=request.url.path
    if path in {'/','/app','/login'} or path.startswith('/static/'):
        response.headers['Cache-Control']='no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma']='no-cache'
        response.headers['Expires']='0'
    return response

class Credentials(BaseModel):
    username:str
    password:str
    display_name:str|None=None

class CreateUser(Credentials):
    is_admin:bool=False
    open_after_create:bool=True

class UrlTrack(BaseModel):
    url:str
    title:str|None=None
    artist:str|None=None

class LibraryTrack(BaseModel): track_id:int
class Reorder(BaseModel): ordered_ids:list[int]
class PositionMove(BaseModel): position:int=Field(ge=1)
class StreamSettingsIn(BaseModel):
    host:str; port:int=Field(ge=1,le=65535); password:str=''; sid:int=Field(default=1,ge=1)
    name:str='Tasia Radio'; genre:str=''; url:str=''; public:bool=False
    bitrate:int=Field(default=192,ge=32,le=320); sample_rate:int=Field(default=44100,ge=8000,le=192000)
    autoplay_library:bool=True; auto_start:bool=False
class SourceIn(BaseModel):
    name:str; kind:str; url:str; username:str=''; password:str=''; root_path:str=''
class SourceItem(BaseModel): source_id:int; path:str
class CatalogSettingsIn(BaseModel):
    base_url:str=''
    client_id:str=''
    client_secret:str=''
    api_key:str=''
    bearer_token:str=''
    clear_client_secret:bool=False
    clear_bearer_token:bool=False
class CatalogItem(BaseModel):
    provider:str
    track_id:str
class FavoriteCatalogIn(CatalogItem):
    title:str
    artist:str=''
    duration:float|None=None
    source_url:str=''
    artwork:str=''
class FavoriteSourceIn(SourceItem):
    title:str=''
    artist:str=''
    duration:float|None=None
class PasswordChange(BaseModel): current_password:str; new_password:str
class SunoCookieIn(BaseModel): cookie:str=Field(min_length=1,max_length=524288)
class SunoSessionIn(BaseModel):
    client_cookie:str=''
    token:str=''  # legacy raw Bearer fallback
    device_id:str=''
class SunoConnectorIn(BaseModel):
    connector_key:str=Field(min_length=20,max_length=256)
    clerk_client_cookie:str=''
    token:str=''  # accepted only for old connector compatibility
    device_id:str=''

class AISettingsIn(BaseModel):
    base_url:str=''
    api_key:str=''
    model:str=''
    system_prompt:str=''
    clear_api_key:bool=False

class AIAdviceIn(BaseModel):
    prompt:str=Field(min_length=1,max_length=4000)
    folder:str=''
    search:str=''


@app.on_event('startup')
def startup():
    MUSIC_DIR.mkdir(parents=True,exist_ok=True); USER_DATA_DIR.mkdir(parents=True,exist_ok=True)
    db.init_db()
    # Engines are started after API is live so their /internal/next callbacks work.
    # A container restart cannot still have a real on-air track. Reset stale
    # prefetch/current-track state, then auto-start only profiles that request it.
    existing_users=db.list_users()
    migrate_legacy_shared_music(existing_users,db)
    for user in existing_users:
        ensure_user_storage(user)
        repair_user_storage(user,db)
        db.normalize_list_positions(user['id'],'queue')
        db.normalize_list_positions(user['id'],'playlist')
        db.reset_reserved_queue(user['id'])
        db.set_state(user['id'],'now_playing',None)
        if not db.get_stream_settings(user['id']).get('auto_start'):
            db.set_state(user['id'],'playout_state','stopped')
    import threading
    threading.Timer(1.0,engine.autostart_users).start()

@app.on_event('shutdown')
def shutdown(): engine.shutdown_all()

@app.get('/',response_class=HTMLResponse)
def landing(): return FileResponse(BASE_DIR/'templates'/'landing.html')

@app.get('/app',response_class=HTMLResponse)
def workstation(): return FileResponse(BASE_DIR/'templates'/'index.html')

@app.get('/login',response_class=HTMLResponse)
def login_page(): return FileResponse(BASE_DIR/'templates'/'index.html')

@app.get('/api/health')
def health(): return {'ok':True,'version':'2.0.0-beta29','time':time.time()}

@app.get('/api/suno/connector/download')
def download_suno_connector():
    path=BASE_DIR.parent/'extras'/'tasia-suno-connector.zip'
    if not path.exists(): raise HTTPException(404,'Tasia Suno Connector package is missing from this build')
    return FileResponse(path,media_type='application/zip',filename='tasia-suno-connector.zip')


def _session_token(request:Request)->str|None:
    auth=request.headers.get('authorization','')
    if auth.lower().startswith('bearer '): return auth[7:].strip()
    return request.cookies.get('tasia_session')

def current_user(request:Request)->dict:
    tok=_session_token(request)
    if not tok: raise HTTPException(401,'Login required')
    user=db.session_user(token_hash(tok))
    if not user: raise HTTPException(401,'Session expired or invalid')
    return user

def admin_user(user:dict=Depends(current_user))->dict:
    if not user.get('is_admin'): raise HTTPException(403,'Admin account required')
    return user

def _set_session(response:Response,user_id:int,request:Request)->str:
    token=new_session_token(); db.create_session(token_hash(token),user_id,expiry_iso(SESSION_DAYS))
    response.set_cookie('tasia_session',token,max_age=SESSION_DAYS*86400,httponly=True,samesite='lax',secure=request.url.scheme=='https',path='/')
    return token

@app.get('/api/auth/setup-status')
def setup_status(): return {'needs_setup':db.user_count()==0,'registration_enabled':ALLOW_REGISTRATION}

@app.post('/api/auth/setup')
def setup_account(body:Credentials,response:Response,request:Request):
    if db.user_count()!=0: raise HTTPException(409,'Initial account already exists')
    try: user=db.create_user(body.username,body.display_name or body.username,hash_password(body.password),True)
    except (ValueError,Exception) as exc:
        if 'UNIQUE' in str(exc).upper(): raise HTTPException(409,'Username already exists')
        if isinstance(exc,ValueError): raise HTTPException(400,str(exc))
        raise
    adopted=db.adopt_legacy_for_first_user(user['id']); migrate_legacy_shared_music([user],db); ensure_user_storage(user); repair_user_storage(user,db); scan_library(user['id'])
    db.set_state(user['id'],'playout_state','stopped'); _set_session(response,user['id'],request)
    out=dict(user); out['music_folder']=str(user_music_root(user))
    return {'ok':True,'user':out,'adopted':adopted}

@app.post('/api/auth/login')
def login(body:Credentials,response:Response,request:Request):
    user=db.find_user(body.username)
    if not user or not verify_password(body.password,user['password_hash']): raise HTTPException(401,'Incorrect username or password')
    safe={k:user[k] for k in ('id','username','display_name','is_admin','created_at')}; safe['music_folder']=str(ensure_user_storage(user)); _set_session(response,user['id'],request)
    return {'ok':True,'user':safe}

@app.post('/api/auth/register')
def register(body:Credentials,response:Response,request:Request):
    if not ALLOW_REGISTRATION: raise HTTPException(403,'Registration is disabled')
    try: user=db.create_user(body.username,body.display_name or body.username,hash_password(body.password),False)
    except ValueError as exc: raise HTTPException(400,str(exc))
    root=ensure_user_storage(user); scan_library(user['id']); db.set_state(user['id'],'playout_state','stopped'); _set_session(response,user['id'],request)
    out=dict(user); out['music_folder']=str(root)
    return {'ok':True,'user':out}

@app.post('/api/auth/logout')
def logout(response:Response,request:Request):
    tok=_session_token(request)
    if tok: db.delete_session(token_hash(tok))
    response.delete_cookie('tasia_session',path='/'); return {'ok':True}

@app.get('/api/me')
def me(user:dict=Depends(current_user)):
    out=dict(user); out['music_folder']=str(ensure_user_storage(user)); return out

@app.post('/api/account/password')
def change_password(body:PasswordChange,response:Response,request:Request,user:dict=Depends(current_user)):
    full=db.find_user(user['username'])
    if not full or not verify_password(body.current_password,full['password_hash']): raise HTTPException(400,'Current password is incorrect')
    try: new_hash=hash_password(body.new_password)
    except ValueError as exc: raise HTTPException(400,str(exc))
    db.update_password(user['id'],new_hash)
    _set_session(response,user['id'],request)
    return {'ok':True}

@app.get('/api/users')
def users(_:dict=Depends(admin_user)): return db.list_users()

@app.post('/api/users')
def create_user_admin(body:CreateUser,response:Response,request:Request,_:dict=Depends(admin_user)):
    try:
        user=db.create_user(body.username,body.display_name or body.username,hash_password(body.password),body.is_admin)
        root=ensure_user_storage(user); scan_library(user['id']); db.set_state(user['id'],'playout_state','stopped')
        switched=False
        if body.open_after_create:
            # Do not invalidate the old admin session before the browser has
            # accepted the replacement cookie. Keeping it until expiry avoids
            # a beta2 race that could leave the UI with HTTP 401 immediately
            # after creating/switching to the new account.
            _set_session(response,user['id'],request); switched=True
        out=dict(user); out['music_folder']=str(root)
        return {'ok':True,'user':out,'switched':switched}
    except ValueError as exc: raise HTTPException(400,str(exc))


def scan_library(user_id:int)->int:
    user=db.user_by_id(user_id)
    if not user: return 0
    root=ensure_user_storage(user)
    db.repair_user_music_paths(user_id,MUSIC_DIR,root)
    db.prune_cross_user_music(user_id,MUSIC_DIR,root)
    db.remove_missing_local_library(user_id,root)
    count=0
    for path in root.rglob('*'):
        if not valid_local_audio(path): continue
        duration,ok=ffprobe(path)
        if not ok: continue
        title,artist=read_tags(path)
        try:
            rel=path.parent.relative_to(root)
            folder='/' if str(rel)=='.' else rel.as_posix()
        except ValueError:
            folder='/'
        db.upsert_library(user_id,path.resolve(),title,artist,duration,path.stat().st_size,folder=folder,source_kind='local')
        count+=1
    return count


def _safe_library_folder(user:dict, folder:str|None='')->tuple[Path,Path,str]:
    root=ensure_user_storage(user).resolve()
    raw=str(folder or '').replace('\\','/').strip('/')
    parts=[p for p in raw.split('/') if p not in {'','.'}]
    if any(p=='..' for p in parts): raise HTTPException(400,'Invalid folder path')
    target=(root.joinpath(*parts)).resolve()
    try: target.relative_to(root)
    except ValueError: raise HTTPException(403,'Folder is outside your private music library')
    return root,target,'/'.join(parts)


def _library_bulk_rows(user_id:int, folder:str|None='', query:str='', recursive:bool=True)->list[dict]:
    if query.strip():
        return db.list_library(user_id,query=query,limit=100000)
    return db.library_tree_rows(user_id,folder=folder or '',recursive=recursive,limit=100000)


def _queue_library_rows(user_id:int, rows:list[dict])->int:
    added=0
    for t in rows:
        path=Path(t['path'])
        if not path.exists():
            continue
        db.add_queue(user_id,path,t['title'],t['artist'],'library',None,t.get('duration')); added+=1
    return added


def _playlist_library_rows(user_id:int, rows:list[dict])->int:
    added=0
    for t in rows:
        path=Path(t['path'])
        if not path.exists():
            continue
        db.add_playlist(user_id,path,t['title'],t['artist'],'library',None,t.get('duration')); added+=1
    return added


def _ai_chat_url(base_url:str)->str:
    base=base_url.strip().rstrip('/')
    if not base:
        raise HTTPException(400,'AI Base URL is not configured')
    if not (base.startswith('http://') or base.startswith('https://')):
        raise HTTPException(400,'AI Base URL must start with http:// or https://')
    if base.endswith('/chat/completions'):
        return base
    if base.endswith('/v1'):
        return base+'/chat/completions'
    return base+'/v1/chat/completions'


def _call_ai(user_id:int,messages:list[dict],max_tokens:int=700)->str:
    settings=db.get_ai_settings(user_id)
    model=str(settings.get('model') or '').strip()
    if not model:
        raise HTTPException(400,'AI model is not configured')
    url=_ai_chat_url(str(settings.get('base_url') or ''))
    headers={'Content-Type':'application/json'}
    key=str(settings.get('api_key') or '').strip()
    if key:
        headers['Authorization']=f'Bearer {key}'
    payload={'model':model,'messages':messages,'temperature':0.6,'max_tokens':max_tokens}
    try:
        with httpx.Client(timeout=httpx.Timeout(60.0,connect=15.0),follow_redirects=True) as client:
            response=client.post(url,headers=headers,json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(502,f'AI connection failed: {exc}') from exc
    if response.status_code>=400:
        detail=response.text.strip().replace('\n',' ')[:500]
        raise HTTPException(502,f'AI endpoint returned HTTP {response.status_code}: {detail or response.reason_phrase}')
    try:
        data=response.json()
        content=data['choices'][0]['message']['content']
        if isinstance(content,list):
            content=''.join(str(part.get('text') or '') if isinstance(part,dict) else str(part) for part in content)
        text=str(content or '').strip()
    except Exception as exc:
        raise HTTPException(502,'AI endpoint returned an unsupported OpenAI-compatible response') from exc
    if not text:
        raise HTTPException(502,'AI endpoint returned an empty answer')
    return text


def _progress(user_id:int)->dict|None:
    np=db.get_state(user_id,'now_playing')
    if not isinstance(np,dict): return None
    out=dict(np)
    try:
        started=float(out.get('started_at_epoch') or 0); paused_total=float(out.get('paused_total') or 0); paused_at=out.get('paused_at_epoch')
        effective=float(paused_at) if paused_at not in (None,'') else time.time(); elapsed=max(0,effective-started-paused_total) if started else 0
    except Exception: elapsed=0
    try: duration=float(out['duration']) if out.get('duration') not in (None,'') else None
    except Exception: duration=None
    if duration and duration>0:
        elapsed=min(elapsed,duration); out['remaining']=max(0,duration-elapsed); out['progress_percent']=max(0,min(100,elapsed/duration*100))
    else: out['remaining']=None; out['progress_percent']=None
    out['elapsed']=elapsed; return out


def _safe_duration(value:Any)->float|None:
    try:
        number=float(value)
    except (TypeError,ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _safe_list_row(row:dict)->dict:
    # Imported/provider metadata is untrusted. One malformed duration or non-string
    # label must never make /api/queue or /api/playlist fail JSON serialization.
    clean=dict(row)
    clean['title']=str(clean.get('title') or 'Untitled')[:1000]
    clean['artist']=str(clean.get('artist') or '')[:1000]
    clean['path']=str(clean.get('path') or '')
    clean['source_type']=str(clean.get('source_type') or 'unknown')[:80]
    clean['source_url']=str(clean.get('source_url') or '') or None
    clean['duration']=_safe_duration(clean.get('duration'))
    return clean


def _local_file_path(value:Any)->Path|None:
    """Return an existing local file path, never stat provider/catalog references.

    Queue/playlist `path` may intentionally contain virtual references such as
    `catalog:universal:<payload>`.  pathlib would treat those as relative file names
    and may raise ENAMETOOLONG before we even get a chance to resolve the provider.
    All real Tasia media/cache paths are absolute filesystem paths.
    """
    raw=str(value or '').strip()
    if not raw or not raw.startswith('/'):
        return None
    try:
        path=Path(raw)
        return path if path.is_file() else None
    except (OSError,ValueError):
        return None


def _is_previewable(value:Any)->bool:
    return _local_file_path(value) is not None


def _timed_queue(user_id:int)->tuple[list[dict],dict]:
    rows=[_safe_list_row(r) for r in db.list_queue(user_id)]; np=_progress(user_id); state=db.get_state(user_id,'playout_state','stopped'); now=time.time()
    on_air=state=='playing' and engine.status(user_id).get('output_active') is True
    remaining=_safe_duration(np.get('remaining')) if np else 0.0; remaining=remaining or 0.0; cursor=remaining
    audio_seconds=0.0
    for row in rows:
        row['offset_seconds']=cursor; row['expected_start_epoch']=now+cursor if on_air else None
        row['previewable']=_is_previewable(row.get('path'))
        dur=row['duration'] or 0.0; audio_seconds+=dur; cursor+=dur
    return rows,{'queue_tracks':len(rows),'queue_audio_seconds':audio_seconds,'set_remaining_seconds':cursor,
                 'set_end_epoch':now+cursor if on_air and (rows or np) else None}


def _timed_playlist(user_id:int)->tuple[list[dict],dict]:
    rows=[_safe_list_row(r) for r in db.list_playlist(user_id)]; cursor=0.0; now=time.time()
    for row in rows:
        row['offset_seconds']=cursor; row['if_started_now_epoch']=now+cursor
        row['previewable']=_is_previewable(row.get('path'))
        cursor+=row['duration'] or 0.0
    return rows,{'playlist_tracks':len(rows),'playlist_seconds':cursor,'playlist_end_if_now_epoch':now+cursor if rows else None}

@app.get('/api/status')
def status(user:dict=Depends(current_user)):
    uid=user['id']; q,qs=_timed_queue(uid); _,ps=_timed_playlist(uid)
    return {'now_playing':_progress(uid),'playout_state':db.get_state(uid,'playout_state','stopped'),'shoutcast':engine.status(uid),'playback_error':db.get_state(uid,'playback_error'),
            'library_count':len(db.list_library(uid,limit=100000)),'queue_summary':qs,'playlist_summary':ps,'user':user}

@app.get('/api/queue')
def queue(user:dict=Depends(current_user)): return _timed_queue(user['id'])[0]

@app.post('/api/queue/reorder')
def reorder_queue(body:Reorder,user:dict=Depends(current_user)):
    try: db.reorder_queue(user['id'],body.ordered_ids)
    except ValueError as exc: raise HTTPException(409,str(exc))
    return {'ok':True}

@app.post('/api/queue/{item_id}/position')
def move_queue_position(item_id:int,body:PositionMove,user:dict=Depends(current_user)):
    try: position=db.move_queue_to_position(user['id'],item_id,body.position)
    except ValueError as exc: raise HTTPException(409,str(exc))
    return {'ok':True,'position':position}

@app.delete('/api/queue/{item_id}')
def delete_queue(item_id:int,user:dict=Depends(current_user)):
    try: ok=db.remove_queue(user['id'],item_id)
    except ValueError as exc: raise HTTPException(409,str(exc))
    if not ok: raise HTTPException(404,'Queue item not found')
    return {'ok':True}

@app.post('/api/queue/clear')
def clear_queue(user:dict=Depends(current_user)): db.clear_queue(user['id']); return {'ok':True}

@app.post('/api/queue/library')
def queue_library(body:LibraryTrack,user:dict=Depends(current_user)):
    t=_library_track(user['id'],body.track_id); return {'ok':True,'queue_id':db.add_queue(user['id'],Path(t['path']),t['title'],t['artist'],'library',None,t.get('duration'))}

@app.post('/api/queue/url')
def queue_url(body:UrlTrack,user:dict=Depends(current_user)):
    path,dur,title,artist,source_url=_prepare_url(user['id'],body)
    return {'ok':True,'queue_id':db.add_queue(user['id'],path,title,artist,'suno' if source_url.startswith('suno:') else 'remote',source_url,dur),'cached_path':str(path),'duration':dur,'resolved_url':source_url}

@app.post('/api/queue/all-library')
def queue_all_library(user:dict=Depends(current_user)): return {'ok':True,'queued':db.queue_all_library(user['id'])}

class LibraryBulk(BaseModel):
    folder:str|None=''
    q:str=''
    recursive:bool=True

@app.post('/api/playlist/all-library')
def playlist_all_library(body:LibraryBulk,user:dict=Depends(current_user)):
    rows=_library_bulk_rows(user['id'],body.folder,body.q,body.recursive)
    return {'ok':True,'added':_playlist_library_rows(user['id'],rows)}

@app.post('/api/library/bulk/queue')
def queue_library_bulk(body:LibraryBulk,user:dict=Depends(current_user)):
    _safe_library_folder(user,body.folder)
    rows=_library_bulk_rows(user['id'],body.folder,body.q,body.recursive)
    return {'ok':True,'queued':_queue_library_rows(user['id'],rows)}

@app.post('/api/library/bulk/playlist')
def playlist_library_bulk(body:LibraryBulk,user:dict=Depends(current_user)):
    _safe_library_folder(user,body.folder)
    rows=_library_bulk_rows(user['id'],body.folder,body.q,body.recursive)
    return {'ok':True,'added':_playlist_library_rows(user['id'],rows)}
@app.post('/api/queue/all-playlist')
def queue_all_playlist(user:dict=Depends(current_user)):
    added=0; failed=[]
    for row in db.list_playlist(user['id']):
        parsed=catalogs.parse_path(str(row.get('path') or ''))
        try:
            if parsed:
                _catalog_add(CatalogItem(provider=parsed[0],track_id=parsed[1]),user,'queue')
            else:
                db.add_queue(user['id'],Path(row['path']),row['title'],row['artist'],row['source_type'],row.get('source_url'),row.get('duration'))
            added+=1
        except Exception as exc:
            failed.append({'id':row.get('id'),'title':row.get('title'),'error':str(getattr(exc,'detail',exc))})
    return {'ok':not failed,'queued':added,'failed':failed}

@app.get('/api/playlist')
def playlist(user:dict=Depends(current_user)): return _timed_playlist(user['id'])[0]

@app.post('/api/playlist/reorder')
def reorder_playlist(body:Reorder,user:dict=Depends(current_user)):
    try: db.reorder_playlist(user['id'],body.ordered_ids)
    except ValueError as exc: raise HTTPException(409,str(exc))
    return {'ok':True}

@app.post('/api/playlist/{item_id}/position')
def move_playlist_position(item_id:int,body:PositionMove,user:dict=Depends(current_user)):
    try: position=db.move_playlist_to_position(user['id'],item_id,body.position)
    except ValueError as exc: raise HTTPException(409,str(exc))
    return {'ok':True,'position':position}

@app.post('/api/playlist/library')
def playlist_library(body:LibraryTrack,user:dict=Depends(current_user)):
    t=_library_track(user['id'],body.track_id); return {'ok':True,'playlist_id':db.add_playlist(user['id'],Path(t['path']),t['title'],t['artist'],'library',None,t.get('duration'))}

@app.post('/api/playlist/url')
def playlist_url(body:UrlTrack,user:dict=Depends(current_user)):
    path,dur,title,artist,source_url=_prepare_url(user['id'],body)
    return {'ok':True,'playlist_id':db.add_playlist(user['id'],path,title,artist,'suno' if source_url.startswith('suno:') else 'remote',source_url,dur),'resolved_url':source_url}

@app.post('/api/playlist/{item_id}/queue')
def queue_playlist_item(item_id:int,user:dict=Depends(current_user)):
    t=db.playlist_by_id(user['id'],item_id)
    if not t: raise HTTPException(404,'Playlist item not found')
    parsed=catalogs.parse_path(str(t.get('path') or ''))
    if parsed:
        return _catalog_add(CatalogItem(provider=parsed[0],track_id=parsed[1]),user,'queue')
    return {'ok':True,'queue_id':db.add_queue(user['id'],Path(t['path']),t['title'],t['artist'],t['source_type'],t.get('source_url'),t.get('duration'))}

@app.delete('/api/playlist/{item_id}')
def delete_playlist(item_id:int,user:dict=Depends(current_user)):
    if not db.remove_playlist(user['id'],item_id): raise HTTPException(404,'Playlist item not found')
    return {'ok':True}
@app.post('/api/playlist/clear')
def clear_playlist(user:dict=Depends(current_user)): db.clear_playlist(user['id']); return {'ok':True}

def _txt_norm(value:str)->str:
    import re
    return re.sub(r'[^a-z0-9]+',' ',str(value or '').lower()).strip()


def _txt_local_index(user_id:int)->dict[str,dict]:
    index={}
    for row in db.list_library(user_id,limit=100000):
        title=str(row.get('title') or '')
        artist=str(row.get('artist') or '')
        path=Path(str(row.get('path') or ''))
        keys={_txt_norm(title),_txt_norm(f'{artist} - {title}'),_txt_norm(f'{title} - {artist}'),_txt_norm(path.stem)}
        for key in keys:
            if key and key not in index:
                index[key]=row
    return index


def _txt_existing_keys(user_id:int,target:str)->set[str]:
    if target=='queue': rows=db.list_queue(user_id)
    elif target=='playlist': rows=db.list_playlist(user_id)
    else: rows=db.list_favorites(user_id,limit=100000)
    keys=set()
    for row in rows:
        provider=str(row.get('provider') or row.get('source_type') or row.get('kind') or '').lower()
        source=str(row.get('source_url') or row.get('remote_path') or row.get('path') or row.get('track_id') or '')
        title=str(row.get('title') or ''); artist=str(row.get('artist') or '')
        if source: keys.add(f'src|{provider}|{source}'.lower())
        if title: keys.add(f'name|{_txt_norm(title)}|{_txt_norm(artist)}')
    return keys


def _txt_result_keys(provider:str,track:dict)->set[str]:
    provider=provider.lower()
    source=str(track.get('url') or track.get('source_url') or track.get('path') or track.get('id') or '')
    title=_txt_norm(track.get('title') or ''); artist=_txt_norm(track.get('artist') or '')
    keys=set()
    if source:
        keys.add(f'src|{provider}|{source}'.lower())
        if provider=='local': keys.add(f'src|library|{source}'.lower())
        if provider=='direct': keys.add(f'src|remote|{source}'.lower())
    if title: keys.add(f'name|{title}|{artist}')
    return keys


def _txt_add_local(user:dict,row:dict,target:str)->dict:
    if target=='queue':
        item=db.add_queue(user['id'],Path(row['path']),row['title'],row.get('artist') or '','library',None,row.get('duration'))
        return {'id':item,'provider':'local','title':row['title'],'artist':row.get('artist') or ''}
    if target=='playlist':
        item=db.add_playlist(user['id'],Path(row['path']),row['title'],row.get('artist') or '','library',None,row.get('duration'))
        return {'id':item,'provider':'local','title':row['title'],'artist':row.get('artist') or ''}
    fav=db.upsert_favorite(user['id'],fingerprint=f"library|{row['id']}",kind='library',provider='local',track_id=str(row['id']),
                           title=row['title'],artist=row.get('artist') or '',duration=row.get('duration'),library_id=int(row['id']))
    return {'id':fav['id'],'provider':'local','title':row['title'],'artist':row.get('artist') or ''}


def _txt_add_url(user:dict,value:str,target:str)->dict:
    resolved,suno_uuid=resolve_suno_url(value,user['id'])
    provider='suno' if suno_uuid else 'direct'
    if target=='favorites':
        track_id=suno_uuid or resolved
        title=f'Suno {suno_uuid[:8]}' if suno_uuid else Path(value).name or 'Direct audio'
        fav=db.upsert_favorite(user['id'],fingerprint=f'url|{provider}|{track_id}',kind='url',provider=provider,track_id=track_id,
                               title=title,artist='',source_url=(f'suno:{suno_uuid}' if suno_uuid else resolved))
        return {'id':fav['id'],'provider':provider,'title':title,'artist':'','url':(f'suno:{suno_uuid}' if suno_uuid else resolved)}
    path,dur,title,artist,source_url=_prepare_url(user['id'],UrlTrack(url=value))
    if target=='queue': item=db.add_queue(user['id'],path,title,artist,provider if provider=='suno' else 'remote',source_url,dur)
    else: item=db.add_playlist(user['id'],path,title,artist,provider if provider=='suno' else 'remote',source_url,dur)
    return {'id':item,'provider':provider,'title':title,'artist':artist,'url':source_url}


def _catalog_result_to_target(user:dict,provider:str,track:dict,target:str)->dict:
    provider=provider.lower().strip()
    if provider not in catalogs.PROVIDERS:
        raise ValueError('Unsupported catalog provider')
    if target=='favorites':
        fav=db.upsert_favorite(user['id'],fingerprint=f"catalog|{provider}|{track['id']}",kind='catalog',provider=provider,track_id=str(track['id']),
                               title=track['title'],artist=track.get('artist') or '',duration=track.get('duration'),source_url=track.get('url') or '',artwork=track.get('artwork') or '')
        return {'id':fav['id'],'provider':provider,'title':track['title'],'artist':track.get('artist') or '','url':track.get('url') or '','track_id':str(track['id'])}
    result=_catalog_add(CatalogItem(provider=provider,track_id=str(track['id'])),user,target)
    return {'id':result.get('queue_id') or result.get('playlist_id'),'provider':provider,'title':track['title'],'artist':track.get('artist') or '','url':track.get('url') or '','track_id':str(track['id'])}


def _searchable_catalogs(user_id:int)->list[str]:
    """Online providers that can answer a text song query right now."""
    providers=['universal','btch-spotify','audius']
    sc=db.get_catalog_settings(user_id,'soundcloud')
    if str(sc.get('client_id') or '').strip() and str(sc.get('client_secret') or '').strip():
        providers.append('soundcloud')
    ja=db.get_catalog_settings(user_id,'jamendo')
    if str(ja.get('client_id') or '').strip():
        providers.append('jamendo')
    st=db.get_catalog_settings(user_id,'stremio')
    if str(st.get('base_url') or '').strip():
        providers.append('stremio')
    return providers


def _search_catalogs(user_id:int,query:str,limit:int=30,providers:list[str]|None=None)->list[dict]:
    query=str(query or '').strip()
    if not query:
        return []
    wanted=providers or _searchable_catalogs(user_id)
    wanted=[p for p in wanted if p in catalogs.PROVIDERS]
    if not wanted:
        return []
    per=max(3,min(10,(int(limit)+len(wanted)-1)//len(wanted)+2))
    rows=[]
    # Search providers concurrently so "All sources" does not wait for each
    # network request serially. Individual provider failures are isolated.
    with ThreadPoolExecutor(max_workers=min(6,len(wanted))) as pool:
        jobs={pool.submit(catalogs.search,p,db.get_catalog_settings(user_id,p),query,per):p for p in wanted}
        for fut in as_completed(jobs):
            try:
                result=fut.result()
            except Exception:
                continue
            for row in result or []:
                if isinstance(row,dict) and row.get('id') and row.get('title'):
                    rows.append(row)
    # Dedupe only within the same provider. Keep the same song when Spotify,
    # Universal, Audius, etc. all find it so the user can choose the service.
    seen=set(); unique=[]
    for row in rows:
        provider=str(row.get('provider') or '').lower()
        key=(provider,str(row.get('id') or ''))
        if key in seen:
            continue
        seen.add(key); unique.append(row)
    qn=_txt_norm(query)
    qtokens=set(qn.split())
    def score(row:dict):
        title=_txt_norm(row.get('title') or '')
        artist=_txt_norm(row.get('artist') or '')
        combo=(artist+' '+title).strip()
        tokens=set(combo.split())
        exact=100 if qn in {title,combo,_txt_norm(f"{title} {artist}")} else 0
        contains=30 if qn and qn in combo else 0
        overlap=(len(qtokens & tokens)/max(1,len(qtokens)))*50
        # A small deterministic provider preference makes imports stable while
        # still letting text similarity dominate.
        pref={'btch-spotify':7,'universal':6,'soundcloud':5,'audius':4,'jamendo':3,'stremio':2}.get(str(row.get('provider') or ''),0)
        return exact+contains+overlap+pref
    unique.sort(key=score,reverse=True)
    return unique[:max(1,min(int(limit),50))]


def _txt_provider_from_url(value:str)->str|None:
    from urllib.parse import urlparse
    parsed=urlparse(value)
    if parsed.scheme not in {'http','https'} or not parsed.netloc:
        return None
    host=(parsed.hostname or '').lower()
    if host in universal.SPOTIFY_HOSTS or host.endswith('.spotify.com'):
        return 'btch-spotify'
    if host in {'soundcloud.com','www.soundcloud.com','m.soundcloud.com','on.soundcloud.com'} or host.endswith('.soundcloud.com'):
        return 'btch-soundcloud'
    if host in {'drive.google.com','docs.google.com'} or host.endswith('.drive.google.com'):
        return 'btch-gdrive'
    if host in universal.YT_HOSTS:
        return 'universal'
    return None


def _txt_find_catalog(user:dict,value:str,source:str)->dict:
    source=(source or 'auto').lower().strip()
    url_provider=_txt_provider_from_url(value)
    if url_provider:
        # Explicit URLs always win over the text-search selector. This lets one
        # mixed TXT contain Spotify, SoundCloud, GDrive and normal song names.
        rows=catalogs.search(url_provider,db.get_catalog_settings(user['id'],url_provider),value,1)
    elif source in {'auto','all'}:
        rows=_search_catalogs(user['id'],value,12)
    else:
        provider=source
        if provider not in catalogs.PROVIDERS:
            raise ValueError('Unknown import search service')
        if provider in {'btch-soundcloud','btch-gdrive'}:
            raise ValueError(f'{provider} accepts links; use SoundCloud Search/Auto for song names')
        rows=catalogs.search(provider,db.get_catalog_settings(user['id'],provider),value,6)
    if not rows:
        raise ValueError('No matching song found in the selected service(s)')
    return rows[0]


@app.get('/api/catalog/all/search')
def catalog_search_all(q:str='',limit:int=30,user:dict=Depends(current_user)):
    try: return _search_catalogs(user['id'],q,limit)
    except Exception as exc: raise HTTPException(400,str(exc))


@app.post('/api/import/txt')
async def import_txt_playlist(
    file:UploadFile|None=File(None),
    text:str=Form(''),
    target:str=Form('playlist'),
    source:str=Form('auto'),
    skip_duplicates:bool=Form(True),
    continue_on_error:bool=Form(True),
    user:dict=Depends(current_user),
):
    target=target.lower().strip()
    source=source.lower().strip()
    allowed_sources={'auto','all','universal','btch-spotify','soundcloud','btch-soundcloud','btch-gdrive','audius','jamendo','stremio'}
    if target not in {'playlist','queue','favorites'}: raise HTTPException(400,'Target must be playlist, queue or favorites')
    if source not in allowed_sources: raise HTTPException(400,'Unsupported import search service')
    supplied_text=str(text or '')
    if file is not None and file.filename:
        raw=await file.read(512*1024+1)
        if len(raw)>512*1024: raise HTTPException(400,'TXT playlist is larger than 512 KB')
        try: supplied_text=raw.decode('utf-8-sig')
        except UnicodeDecodeError: supplied_text=raw.decode('cp1250',errors='replace')
    if not supplied_text.strip(): raise HTTPException(400,'Choose a TXT file or paste a song list')
    entries=[]
    for number,line in enumerate(supplied_text.splitlines(),1):
        value=line.strip()
        if not value or value.startswith('#'): continue
        entries.append((number,value))
    if not entries: raise HTTPException(400,'Song list contains no songs')
    if len(entries)>250: raise HTTPException(400,'TXT import is limited to 250 song lines at once')

    local_index=_txt_local_index(user['id'])
    existing=_txt_existing_keys(user['id'],target) if skip_duplicates else set()
    seen=set(); report=[]; added=skipped=failed=0

    from urllib.parse import urlparse
    for line_no,value in entries:
        base={'line':line_no,'input':value}
        norm=_txt_norm(value)
        if skip_duplicates and norm in seen:
            report.append({**base,'status':'skipped','detail':'Duplicate line in list'}); skipped+=1; continue
        seen.add(norm)
        try:
            # Auto/all keeps the old nice behavior: exact private-library match
            # wins before any network search. Selecting a specific service
            # deliberately bypasses local matching.
            local=local_index.get(norm) if source in {'auto','all'} else None
            if local:
                candidate={'title':local['title'],'artist':local.get('artist') or '','path':local['path']}
                keys=_txt_result_keys('local',candidate)
                if skip_duplicates and existing.intersection(keys):
                    report.append({**base,'status':'skipped','title':local['title'],'provider':'Local','detail':'Already in target'}); skipped+=1; continue
                out=_txt_add_local(user,local,target)
            else:
                parsed=urlparse(value)
                is_http=parsed.scheme in {'http','https'} and bool(parsed.netloc)
                # Suno remains its own authenticated resolver. Normal direct
                # URLs stay direct unless they are a recognised catalog URL.
                resolved,suno_uuid=resolve_suno_url(value,user['id'])
                is_suno=bool(suno_uuid)
                url_provider=_txt_provider_from_url(value)
                if is_suno:
                    preview={'title':f'Suno {suno_uuid[:8]}','artist':'','url':resolved}
                    keys=_txt_result_keys('suno',preview)
                    if skip_duplicates and existing.intersection(keys):
                        report.append({**base,'status':'skipped','title':preview['title'],'provider':'Suno','detail':'Already in target'}); skipped+=1; continue
                    out=_txt_add_url(user,value,target)
                elif is_http and not url_provider:
                    preview={'title':Path(parsed.path).name or 'Direct audio','artist':'','url':resolved}
                    keys=_txt_result_keys('direct',preview)
                    if skip_duplicates and existing.intersection(keys):
                        report.append({**base,'status':'skipped','title':preview['title'],'provider':'Direct','detail':'Already in target'}); skipped+=1; continue
                    out=_txt_add_url(user,value,target)
                else:
                    track=_txt_find_catalog(user,value,source)
                    provider=str(track.get('provider') or '')
                    keys=_txt_result_keys(provider,track)
                    if skip_duplicates and existing.intersection(keys):
                        report.append({**base,'status':'skipped','title':track['title'],'artist':track.get('artist') or '','provider':provider,'detail':'Already in target'}); skipped+=1; continue
                    out=_catalog_result_to_target(user,provider,track,target)
                keys=_txt_result_keys(out.get('provider') or '',out)
            existing.update(keys)
            report.append({**base,'status':'added','title':out.get('title') or value,'artist':out.get('artist') or '','provider':str(out.get('provider') or '')})
            added+=1
        except Exception as exc:
            detail=str(getattr(exc,'detail',exc)) or exc.__class__.__name__
            report.append({**base,'status':'failed','detail':detail[:700]}); failed+=1
            if not continue_on_error: break

    if target in {'playlist','queue'}:
        db.normalize_list_positions(user['id'],target)
    return {'ok':failed==0,'target':target,'source':source,'summary':{'lines':len(entries),'added':added,'skipped':skipped,'failed':failed},'report':report}


@app.get('/api/library')
def library(folder:str|None=None,q:str='',user:dict=Depends(current_user)): return db.list_library(user['id'],folder=folder,query=q)

def _private_preview_path(user:dict,row:dict)->Path:
    # Preview is allowed only from this account's private music tree or private
    # data/cache tree. Queue/playlist rows can contain cached Universal/Suno/
    # Jellyfin/WebDAV/FTP audio under /data/users/<id>, not only /music/users.
    path=Path(str(row.get('path') or '')).resolve()
    roots=(ensure_user_storage(user).resolve(),(USER_DATA_DIR/str(int(user['id']))).resolve())
    allowed=False
    for root in roots:
        try:
            path.relative_to(root); allowed=True; break
        except ValueError:
            pass
    if not allowed:
        raise HTTPException(403,'Preview file is outside your private Tasia storage')
    if not valid_local_audio(path):
        raise HTTPException(400,'This item has no cached/local audio available for browser preview')
    return path


@app.get('/api/library/preview/{track_id}')
def preview_library(track_id:int,user:dict=Depends(current_user)):
    return FileResponse(_private_preview_path(user,_library_track(user['id'],track_id)))


@app.get('/api/queue/{item_id}/preview')
def preview_queue_item(item_id:int,user:dict=Depends(current_user)):
    row=db.queue_by_id(user['id'],item_id)
    if not row: raise HTTPException(404,'Queue item not found')
    return FileResponse(_private_preview_path(user,row))


@app.get('/api/playlist/{item_id}/preview')
def preview_playlist_item(item_id:int,user:dict=Depends(current_user)):
    row=db.playlist_by_id(user['id'],item_id)
    if not row: raise HTTPException(404,'Playlist item not found')
    return FileResponse(_private_preview_path(user,row))

@app.get('/api/library/browse')
def library_browse(folder:str='',q:str='',user:dict=Depends(current_user)):
    root,target,rel=_safe_library_folder(user,folder)
    query=q.strip()
    if query:
        tracks=db.list_library(user['id'],query=query,limit=5000)
        return {'path':rel,'parent':'/'.join(rel.split('/')[:-1]) if rel else '', 'search':query,'folders':[],'tracks':tracks,'stats':{'tracks':len(tracks),'seconds':sum(float(t.get('duration') or 0) for t in tracks)}}
    if not target.exists():
        raise HTTPException(404,'Folder not found; press Scan if it was recently changed')
    stats_map=db.library_stats_map(user['id'])
    folders=[]
    for child in sorted((p for p in target.iterdir() if p.is_dir()),key=lambda p:p.name.lower()):
        try:
            child_rel=child.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        stats=stats_map.get(child_rel,{'tracks':0,'seconds':0.0})
        folders.append({'name':child.name,'path':child_rel,'tracks':stats['tracks'],'seconds':stats['seconds']})
    tracks=db.library_tree_rows(user['id'],folder=rel,recursive=False,limit=5000)
    return {'path':rel,'parent':'/'.join(rel.split('/')[:-1]) if rel else '', 'search':'','folders':folders,'tracks':tracks,'stats':stats_map.get(rel,{'tracks':0,'seconds':0.0})}

@app.get('/api/library/folders')
def library_folders(user:dict=Depends(current_user)): return db.library_folders(user['id'])
@app.post('/api/library/scan')
def api_scan(user:dict=Depends(current_user)): return {'ok':True,'found':scan_library(user['id'])}

@app.post('/api/upload')
def upload(file:UploadFile=File(...),user:dict=Depends(current_user)):
    suffix=Path(file.filename or '').suffix.lower()
    if suffix not in AUDIO_EXTENSIONS: raise HTTPException(400,f'Unsupported extension: {suffix or "(none)"}')
    root=ensure_user_storage(user)/'Uploads'; root.mkdir(parents=True,exist_ok=True); safe=Path(file.filename or f'upload{suffix}').name; target=root/safe; n=1
    while target.exists(): target=root/f'{Path(safe).stem}-{n}{suffix}'; n+=1
    with target.open('wb') as h: shutil.copyfileobj(file.file,h)
    duration,ok=ffprobe(target)
    if not ok: target.unlink(missing_ok=True); raise HTTPException(400,'Uploaded file is not playable audio')
    title,artist=read_tags(target); lid=db.upsert_library(user['id'],target.resolve(),title,artist,duration,target.stat().st_size,folder='Uploads',source_kind='local')
    return {'ok':True,'id':lid,'title':title,'artist':artist,'duration':duration}

def _favorite_public(row:dict)->dict:
    return {k:row.get(k) for k in ('id','kind','provider','track_id','title','artist','duration','source_url','artwork','source_id','remote_path','library_id','added_at')}


@app.get('/api/favorites')
def favorites(q:str='',user:dict=Depends(current_user)):
    return [_favorite_public(x) for x in db.list_favorites(user['id'],query=q,limit=5000)]


@app.post('/api/favorites/catalog')
def favorite_catalog(body:FavoriteCatalogIn,user:dict=Depends(current_user)):
    provider=body.provider.lower().strip()
    if provider not in catalogs.PROVIDERS: raise HTTPException(400,'Unsupported catalog provider')
    if not body.track_id.strip(): raise HTTPException(400,'Missing catalog track id')
    row=db.upsert_favorite(user['id'],fingerprint=f'catalog|{provider}|{body.track_id}',kind='catalog',provider=provider,track_id=body.track_id,
                           title=body.title,artist=body.artist,duration=body.duration,source_url=body.source_url,artwork=body.artwork)
    return {'ok':True,'favorite':_favorite_public(row)}


@app.post('/api/favorites/url')
def favorite_url(body:UrlTrack,user:dict=Depends(current_user)):
    try: resolved,suno_uuid=resolve_suno_url(body.url,user['id'])
    except Exception as exc: raise HTTPException(400,str(exc))
    provider='suno' if suno_uuid else 'direct'
    track_id=suno_uuid or resolved
    title=(body.title or (f'Suno {suno_uuid[:8]}' if suno_uuid else 'Direct audio')).strip()
    stable_source=f'suno:{suno_uuid}' if suno_uuid else resolved
    row=db.upsert_favorite(user['id'],fingerprint=f'url|{provider}|{track_id}',kind='url',provider=provider,track_id=track_id,title=title,
                           artist=(body.artist or '').strip(),source_url=stable_source)
    return {'ok':True,'favorite':_favorite_public(row),'resolved_url':stable_source}


@app.post('/api/favorites/source')
def favorite_source(body:FavoriteSourceIn,user:dict=Depends(current_user)):
    src=db.source_by_id(user['id'],body.source_id,with_password=False)
    if not src: raise HTTPException(404,'Source not found')
    remote_path=body.path.strip()
    if not remote_path: raise HTTPException(400,'Missing source track path')
    provider=str(src.get('kind') or 'remote')
    title=body.title.strip() or Path(remote_path).name or 'Remote track'
    row=db.upsert_favorite(user['id'],fingerprint=f'source|{body.source_id}|{remote_path}',kind='source',provider=provider,track_id='',
                           title=title,artist=body.artist,duration=body.duration,source_url=f'{provider}:{body.source_id}:{remote_path}',
                           source_id=body.source_id,remote_path=remote_path)
    return {'ok':True,'favorite':_favorite_public(row)}


@app.post('/api/favorites/library')
def favorite_library(body:LibraryTrack,user:dict=Depends(current_user)):
    t=_library_track(user['id'],body.track_id)
    row=db.upsert_favorite(user['id'],fingerprint=f'library|{body.track_id}',kind='library',provider='local',track_id=str(body.track_id),
                           title=t['title'],artist=t.get('artist') or '',duration=t.get('duration'),library_id=body.track_id,source_url=str(t.get('remote_path') or ''))
    return {'ok':True,'favorite':_favorite_public(row)}


def _favorite_playout_row(user:dict,row:dict)->dict:
    uid=int(user['id'])
    source_type=str(row.get('source_type') or 'library').lower()
    path_text=str(row.get('path') or '')
    source_url=str(row.get('source_url') or '')
    title=str(row.get('title') or 'Untitled')
    artist=str(row.get('artist') or '')
    duration=row.get('duration')

    # Catalog items that have not been cached use Tasia's synthetic catalog URI.
    # Preserve the provider track id so Saved can resolve it again later.
    parsed=catalogs.parse_path(path_text)
    if source_type in catalogs.PROVIDERS and parsed:
        provider,track_id=parsed
        saved=db.upsert_favorite(uid,fingerprint=f'catalog|{provider}|{track_id}',kind='catalog',provider=provider,track_id=track_id,
                                 title=title,artist=artist,duration=duration,source_url=source_url)
        return _favorite_public(saved)

    # Anything already cached/local should become a library favourite. This is
    # the most reliable party behaviour: replaying the favourite needs no search.
    lib=db.library_by_path(uid,path_text)
    if not lib and path_text:
        try:
            local_path=_private_preview_path(user,row)
            size=local_path.stat().st_size
            folder='Saved / Cached'
            lid=db.upsert_library(uid,local_path,title,artist,duration,size,folder=folder,source_kind=source_type,remote_path=source_url or None)
            lib=db.library_by_id(uid,lid)
        except HTTPException:
            lib=None
    if lib:
        lid=int(lib['id'])
        saved=db.upsert_favorite(uid,fingerprint=f'library|{lid}',kind='library',provider=source_type if source_type!='library' else 'local',track_id=str(lid),
                                 title=title,artist=artist,duration=duration,library_id=lid,source_url=str(lib.get('remote_path') or source_url or ''))
        return _favorite_public(saved)

    # Suno/direct HTTP rows can still be saved by their stable upstream URL.
    if (source_url.startswith(('http://','https://','suno:')) or source_type=='suno') and source_type!='universal':
        try: resolved,suno_uuid=resolve_suno_url(source_url[5:] if source_url.startswith('suno:') else source_url,uid)
        except Exception:
            resolved,suno_uuid=source_url,None
        provider='suno' if suno_uuid or source_type=='suno' else (source_type or 'direct')
        track_id=suno_uuid or resolved
        saved=db.upsert_favorite(uid,fingerprint=f'url|{provider}|{track_id}',kind='url',provider=provider,track_id=track_id,
                                 title=title,artist=artist,duration=duration,source_url=(f'suno:{suno_uuid}' if suno_uuid else resolved))
        return _favorite_public(saved)

    raise HTTPException(400,'This track cannot be saved yet because it has no reusable source or cached audio')


@app.post('/api/queue/{item_id}/favorite')
def favorite_queue_item(item_id:int,user:dict=Depends(current_user)):
    row=db.queue_by_id(user['id'],item_id)
    if not row: raise HTTPException(404,'Queue item not found')
    return {'ok':True,'favorite':_favorite_playout_row(user,row)}


@app.post('/api/playlist/{item_id}/favorite')
def favorite_playlist_item(item_id:int,user:dict=Depends(current_user)):
    row=db.playlist_by_id(user['id'],item_id)
    if not row: raise HTTPException(404,'Playlist item not found')
    return {'ok':True,'favorite':_favorite_playout_row(user,row)}


def _favorite_add(favorite_id:int,user:dict,target:str):
    fav=db.favorite_by_id(user['id'],favorite_id)
    if not fav: raise HTTPException(404,'Saved track not found')
    kind=fav.get('kind')
    if kind=='catalog':
        return _catalog_add(CatalogItem(provider=fav['provider'],track_id=fav['track_id']),user,target)
    if kind=='url':
        saved_title=str(fav.get('title') or '')
        title_hint=None if fav.get('provider')=='suno' and saved_title.startswith('Suno ') else (saved_title or None)
        body=UrlTrack(url=(fav.get('track_id') if fav.get('provider')=='suno' else (fav.get('source_url') or fav.get('track_id') or '')),title=title_hint,artist=fav.get('artist') or None)
        path,dur,title,artist,source_url=_prepare_url(user['id'],body)
        source_type='suno' if fav.get('provider')=='suno' or source_url.startswith('suno:') else 'remote'
        # Once a saved URL has actually been resolved, improve the favourite with
        # real tags/duration so the Saved library gets better over time.
        db.upsert_favorite(user['id'],fingerprint=fav['fingerprint'],kind='url',provider=fav.get('provider') or source_type,
                           track_id=fav.get('track_id') or source_url,title=title,artist=artist,duration=dur,source_url=source_url,
                           artwork=fav.get('artwork') or '')
        if target=='queue':
            return {'ok':True,'queue_id':db.add_queue(user['id'],path,title,artist,source_type,source_url,dur)}
        return {'ok':True,'playlist_id':db.add_playlist(user['id'],path,title,artist,source_type,source_url,dur)}
    if kind=='source':
        return _source_add(SourceItem(source_id=int(fav.get('source_id') or 0),path=str(fav.get('remote_path') or '')),user,target)
    if kind=='library':
        t=_library_track(user['id'],int(fav.get('library_id') or fav.get('track_id') or 0))
        if target=='queue': return {'ok':True,'queue_id':db.add_queue(user['id'],Path(t['path']),t['title'],t['artist'],'library',None,t.get('duration'))}
        return {'ok':True,'playlist_id':db.add_playlist(user['id'],Path(t['path']),t['title'],t['artist'],'library',None,t.get('duration'))}
    raise HTTPException(400,'Unsupported saved-track type')


@app.post('/api/favorites/{favorite_id}/queue')
def favorite_queue(favorite_id:int,user:dict=Depends(current_user)): return _favorite_add(favorite_id,user,'queue')

@app.post('/api/favorites/{favorite_id}/playlist')
def favorite_playlist(favorite_id:int,user:dict=Depends(current_user)): return _favorite_add(favorite_id,user,'playlist')

@app.delete('/api/favorites/{favorite_id}')
def favorite_delete(favorite_id:int,user:dict=Depends(current_user)):
    if not db.remove_favorite(user['id'],favorite_id): raise HTTPException(404,'Saved track not found')
    return {'ok':True}


@app.post('/api/sources/test')
def source_test(body:SourceIn,user:dict=Depends(current_user)):
    if body.kind not in {'webdav','ftp','jellyfin'}: raise HTTPException(400,'kind must be webdav, ftp or jellyfin')
    candidate={'id':0,'user_id':user['id'],'name':body.name or 'Test source','kind':body.kind,'url':body.url,'username':body.username,'password':body.password,'root_path':body.root_path}
    try: return test_source(candidate)
    except Exception as exc: raise HTTPException(400,str(exc))

@app.get('/api/sources')
def list_sources(user:dict=Depends(current_user)): return db.list_sources(user['id'])
@app.post('/api/sources')
def add_source(body:SourceIn,user:dict=Depends(current_user)):
    if body.kind not in {'webdav','ftp','jellyfin'}: raise HTTPException(400,'kind must be webdav, ftp or jellyfin')
    return {'ok':True,'id':db.add_source(user['id'],body.name,body.kind,body.url,body.username,body.password,body.root_path)}
@app.delete('/api/sources/{source_id}')
def del_source(source_id:int,user:dict=Depends(current_user)):
    if not db.delete_source(user['id'],source_id): raise HTTPException(404,'Source not found')
    return {'ok':True}
@app.get('/api/sources/{source_id}/browse')
def source_browse(source_id:int,path:str='',q:str='',user:dict=Depends(current_user)):
    src=db.source_by_id(user['id'],source_id)
    if not src: raise HTTPException(404,'Source not found')
    try: return browse_source(src,path,q)
    except Exception as exc: raise HTTPException(400,str(exc))
@app.post('/api/sources/item/queue')
def source_queue(body:SourceItem,user:dict=Depends(current_user)): return _source_add(body,user,'queue')
@app.post('/api/sources/item/playlist')
def source_playlist(body:SourceItem,user:dict=Depends(current_user)): return _source_add(body,user,'playlist')


def _catalog_public_settings(row:dict)->dict:
    return {
        'provider':row.get('provider',''),
        'client_id':row.get('client_id',''),
        'api_key':row.get('api_key',''),
        'base_url':row.get('base_url',''),
        'client_secret_set':bool(row.get('client_secret')),
        'bearer_token_set':bool(row.get('bearer_token')),
    }

@app.get('/api/catalog/settings')
def catalog_settings(user:dict=Depends(current_user)):
    return [_catalog_public_settings(x) for x in db.list_catalog_settings(user['id'])]

@app.put('/api/catalog/settings/{provider}')
def save_catalog_settings(provider:str,body:CatalogSettingsIn,user:dict=Depends(current_user)):
    provider=provider.lower()
    if provider not in catalogs.PROVIDERS: raise HTTPException(404,'Unsupported catalog provider')
    values={
        'base_url':body.base_url,
        'client_id':body.client_id,
        'client_secret':'' if body.clear_client_secret else body.client_secret,
        'api_key':body.api_key,
        'bearer_token':'' if body.clear_bearer_token else body.bearer_token,
    }
    saved=db.update_catalog_settings(user['id'],provider,values,keep_secrets_if_blank=True)
    if body.clear_client_secret:
        saved=db.update_catalog_settings(user['id'],provider,{**saved,'client_secret':''},keep_secrets_if_blank=False)
    if body.clear_bearer_token:
        saved=db.update_catalog_settings(user['id'],provider,{**saved,'bearer_token':''},keep_secrets_if_blank=False)
    return {'ok':True,'settings':_catalog_public_settings(saved)}

@app.post('/api/catalog/{provider}/test')
def catalog_test(provider:str,body:CatalogSettingsIn|None=None,user:dict=Depends(current_user)):
    provider=provider.lower()
    if provider not in catalogs.PROVIDERS: raise HTTPException(404,'Unsupported catalog provider')
    settings=db.get_catalog_settings(user['id'],provider)
    if body is not None:
        settings=dict(settings)
        settings['base_url']=body.base_url.strip()
        settings['client_id']=body.client_id.strip()
        settings['api_key']=body.api_key.strip()
        if body.clear_client_secret: settings['client_secret']=''
        elif body.client_secret: settings['client_secret']=body.client_secret
        if body.clear_bearer_token: settings['bearer_token']=''
        elif body.bearer_token: settings['bearer_token']=body.bearer_token
    try: return catalogs.test(provider,settings)
    except Exception as exc: raise HTTPException(400,str(exc))

@app.get('/api/catalog/{provider}/search')
def catalog_search(provider:str,q:str='',limit:int=30,user:dict=Depends(current_user)):
    provider=provider.lower()
    if provider not in catalogs.PROVIDERS: raise HTTPException(404,'Unsupported catalog provider')
    try: return catalogs.search(provider,db.get_catalog_settings(user['id'],provider),q,limit)
    except Exception as exc: raise HTTPException(400,str(exc))

def _catalog_add(body:CatalogItem,user:dict,target:str):
    provider=body.provider.lower()
    if provider not in catalogs.PROVIDERS: raise HTTPException(400,'Unsupported catalog provider')
    settings=db.get_catalog_settings(user['id'],provider)
    try: track=catalogs.get_track(provider,settings,body.track_id)
    except Exception as exc: raise HTTPException(400,str(exc))
    if track.get('access')=='blocked': raise HTTPException(400,f'{provider} says this track is not streamable')
    if provider=='universal' and target=='queue':
        try: path,duration=universal.cache_track(track['id'],user['id'],settings.get('base_url'))
        except Exception as exc: raise HTTPException(400,str(exc))
        track['duration']=duration or track.get('duration')
        db.upsert_library(user['id'],path,track['title'],track['artist'],track.get('duration'),path.stat().st_size,
                          folder='Universal Search',source_kind='universal',remote_path=track.get('url') or '')
    elif provider.startswith('btch-') and target=='queue':
        try: path,duration,_=cache_remote_audio(str(track.get('media_url') or ''),user['id'],filename_hint=track.get('title'))
        except Exception as exc: raise HTTPException(400,str(exc))
        track['duration']=duration or track.get('duration')
        folder={'btch-spotify':'Spotify (BTCH)','btch-soundcloud':'SoundCloud (BTCH)','btch-gdrive':'Google Drive (BTCH)'}.get(provider,'BTCH')
        db.upsert_library(user['id'],path,track['title'],track.get('artist') or '',track.get('duration'),path.stat().st_size,
                          folder=folder,source_kind=provider,remote_path=track.get('url') or '')
    else:
        # Playlist is a set plan, not a download queue. Universal entries stay as
        # provider references here and are converted only when moved to Queue.
        path=catalogs.make_path(provider,track['id'])
    if target=='queue':
        item_id=db.add_queue(user['id'],path,track['title'],track['artist'],provider,track.get('url') or None,track.get('duration'))
        return {'ok':True,'queue_id':item_id,'track':track}
    item_id=db.add_playlist(user['id'],path,track['title'],track['artist'],provider,track.get('url') or None,track.get('duration'))
    return {'ok':True,'playlist_id':item_id,'track':track}

@app.post('/api/catalog/item/queue')
def catalog_queue(body:CatalogItem,user:dict=Depends(current_user)): return _catalog_add(body,user,'queue')

@app.post('/api/catalog/item/playlist')
def catalog_playlist(body:CatalogItem,user:dict=Depends(current_user)): return _catalog_add(body,user,'playlist')


@app.get('/api/universal/status')
def universal_status(user:dict=Depends(current_user)):
    try: return universal.runtime_status(user['id'])
    except Exception as exc: raise HTTPException(400,str(exc))

@app.post('/api/universal/cookies')
async def universal_cookies_upload(file:UploadFile=File(...),user:dict=Depends(current_user)):
    try:
        data=await file.read(5*1024*1024+1)
        universal.save_cookies(user['id'],data)
        return {'ok':True,'cookies_set':True}
    except Exception as exc: raise HTTPException(400,str(exc))

@app.delete('/api/universal/cookies')
def universal_cookies_clear(user:dict=Depends(current_user)):
    universal.clear_cookies(user['id'])
    return {'ok':True,'cookies_set':False}

@app.get('/api/suno/auth/status')
def suno_auth_status(user:dict=Depends(current_user)):
    return {'ok':True,**media_suno_auth_status(user['id'])}


@app.post('/api/suno/session')
def suno_session_manual(body:SunoSessionIn,user:dict=Depends(current_user)):
    try:
        if body.client_cookie.strip():
            status=save_suno_browser_session(user['id'],body.client_cookie,body.device_id or None)
        elif body.token.strip():
            status=save_suno_session(user['id'],body.token,body.device_id or None)
        else:
            raise ValueError('Paste a Suno __client cookie or use the browser connector')
    except ValueError as exc: raise HTTPException(400,str(exc))
    return {'ok':True,**status}


@app.post('/api/suno/session/refresh')
def suno_session_refresh(user:dict=Depends(current_user)):
    try: status=refresh_suno_session(user['id'])
    except ValueError as exc: raise HTTPException(400,str(exc))
    return {'ok':True,**status}


@app.delete('/api/suno/session')
def suno_session_clear(user:dict=Depends(current_user)):
    clear_suno_session(user['id'],keep_connector_key=True)
    return {'ok':True,**media_suno_auth_status(user['id'])}


@app.post('/api/suno/connector/generate')
def suno_connector_generate(user:dict=Depends(current_user)):
    # Idempotent: create a key only if the user does not already have one.
    # This is deliberately independent from the Suno Bearer/session state.
    key=get_suno_connector_key(user['id'])
    return {'ok':True,'connector_key':key}


@app.post('/api/suno/connector/rotate')
def suno_connector_rotate(user:dict=Depends(current_user)):
    key=rotate_suno_connector_key(user['id'])
    return {'ok':True,'connector_key':key}


@app.post('/api/suno/connector/session')
def suno_connector_session(body:SunoConnectorIn):
    # No Tasia browser session is required: pairing is explicitly authorized
    # with the per-user connector key. The connector sends only the user's own
    # Suno Clerk __client cookie + device id, never the full browser cookie jar.
    matched=None
    for candidate in db.list_users():
        key=get_suno_connector_key(candidate['id'])
        if secrets.compare_digest(key,body.connector_key):
            matched=candidate; break
    if not matched: raise HTTPException(401,'Invalid Tasia Suno connector key')
    try:
        if body.clerk_client_cookie.strip():
            status=save_suno_browser_session(matched['id'],body.clerk_client_cookie,body.device_id or None)
        elif body.token.strip():
            # Old connector v1.0 compatibility; this mode is not refreshable.
            status=save_suno_session(matched['id'],body.token,body.device_id or None)
        else:
            raise ValueError('Connector did not provide a Suno __client session cookie')
    except ValueError as exc: raise HTTPException(400,str(exc))
    return {'ok':True,'connected':status['connected'],'refreshable':status.get('refreshable',False),'username':matched['username']}


# Legacy beta22 cookie support remains as a fallback for signed-cookie delivery.
@app.post('/api/suno/cookies')
async def suno_cookies_upload(file:UploadFile=File(...),user:dict=Depends(current_user)):
    data=await file.read(512*1024+1)
    try: status=save_suno_cookies(user['id'],data)
    except ValueError as exc: raise HTTPException(400,str(exc))
    return {'ok':True,**status}


@app.post('/api/suno/cookie-header')
def suno_cookie_header(body:SunoCookieIn,user:dict=Depends(current_user)):
    try: status=save_suno_cookies(user['id'],body.cookie.encode('utf-8'))
    except ValueError as exc: raise HTTPException(400,str(exc))
    return {'ok':True,**status}


@app.delete('/api/suno/cookies')
def suno_cookies_clear(user:dict=Depends(current_user)):
    clear_suno_cookies(user['id'])
    return {'ok':True,**suno_cookie_status(user['id'])}


@app.get('/api/settings/ai')
def ai_settings(user:dict=Depends(current_user)):
    settings=db.get_ai_settings(user['id'])
    return {'base_url':settings.get('base_url') or '', 'model':settings.get('model') or '',
            'system_prompt':settings.get('system_prompt') or '', 'api_key_set':bool(settings.get('api_key'))}

@app.put('/api/settings/ai')
def save_ai_settings(body:AISettingsIn,user:dict=Depends(current_user)):
    values=body.model_dump()
    clear=bool(values.pop('clear_api_key',False))
    if clear:
        values['api_key']=''
    saved=db.update_ai_settings(user['id'],values,keep_api_key_if_blank=not clear)
    return {'ok':True,'settings':{'base_url':saved.get('base_url') or '', 'model':saved.get('model') or '',
                                  'system_prompt':saved.get('system_prompt') or '', 'api_key_set':bool(saved.get('api_key'))}}

@app.post('/api/ai/test')
def ai_test(user:dict=Depends(current_user)):
    answer=_call_ai(user['id'],[{'role':'system','content':'You are a connection test.'},{'role':'user','content':'Reply only with OK.'}],max_tokens=16)
    return {'ok':True,'answer':answer}

@app.post('/api/ai/advice')
def ai_advice(body:AIAdviceIn,user:dict=Depends(current_user)):
    uid=user['id']; settings=db.get_ai_settings(uid)
    if not settings.get('base_url') or not settings.get('model'):
        raise HTTPException(400,'Configure DJ AI Base URL and model in Settings first')
    queue_rows,_=_timed_queue(uid); playlist_rows,_=_timed_playlist(uid); now=_progress(uid)
    candidates=_library_bulk_rows(uid,body.folder,body.search,recursive=True)[:120]
    folders=[]; stats_map=db.library_stats_map(uid)
    for name in db.library_folders(uid)[:80]:
        stats=stats_map.get(str(name or '').strip('/'),{'tracks':0,'seconds':0.0})
        folders.append({'folder':name or '/', 'tracks':stats['tracks'], 'duration_seconds':round(stats['seconds'])})
    context={
        'now_playing': {k:now.get(k) for k in ('title','artist','duration','elapsed','remaining')} if now else None,
        'queue': [{k:r.get(k) for k in ('title','artist','duration','expected_start_epoch')} for r in queue_rows[:30]],
        'playlist': [{k:r.get(k) for k in ('title','artist','duration')} for r in playlist_rows[:40]],
        'library_view': [{k:r.get(k) for k in ('id','title','artist','duration','folder')} for r in candidates],
        'folders': folders,
    }
    system=(str(settings.get('system_prompt') or '').strip() or
            'You are DJ Tasia, a concise radio/party DJ adviser. Suggest practical sequencing and exact tracks from the supplied library when possible. Do not claim you changed playback; you only advise. Keep the answer easy to act on during a live set.')
    messages=[{'role':'system','content':system},{'role':'user','content':f"DJ workstation context:\n{json.dumps(context,ensure_ascii=False)}\n\nRequest: {body.prompt}"}]
    return {'ok':True,'answer':_call_ai(uid,messages,max_tokens=900)}

@app.get('/api/settings/stream')
def get_stream(user:dict=Depends(current_user)):
    s=db.get_stream_settings(user['id']); s['password_set']=bool(s.get('password')); s['password']=''; return s
@app.put('/api/settings/stream')
def put_stream(body:StreamSettingsIn,user:dict=Depends(current_user)):
    uid=user['id']; old=db.get_stream_settings(uid); values=body.model_dump()
    if not values.get('password'): values['password']=old['password']
    was=engine.status(uid); new=db.update_stream_settings(uid,values)
    if was.get('engine_running'):
        try:
            reconnect=was.get('output_active') is True
            engine.stop_engine(uid); engine.ensure(uid,force_restart=True)
            if db.get_state(uid,'playout_state','stopped')!='playing':
                engine.command(uid,'var.set tasia_playout = false',ensure_engine=False)
            if reconnect: engine.connect_output(uid)
        except Exception as exc: db.set_state(uid,'engine_error',str(exc))
    new['password_set']=bool(new.get('password')); new['password']=''; return {'ok':True,'settings':new}


def _clean(raw:str)->str: return '\n'.join(x.strip() for x in raw.replace('\r','').split('\n') if x.strip() and x.strip()!='END').strip()
def _freeze_clock(uid:int):
    np=db.get_state(uid,'now_playing')
    if isinstance(np,dict) and np.get('paused_at_epoch') in (None,''):
        np['paused_at_epoch']=time.time(); db.set_state(uid,'now_playing',np)
def _resume_clock(uid:int):
    np=db.get_state(uid,'now_playing')
    if isinstance(np,dict) and np.get('paused_at_epoch') not in (None,''):
        try: np['paused_total']=float(np.get('paused_total') or 0)+max(0,time.time()-float(np['paused_at_epoch']))
        except Exception: pass
        np['paused_at_epoch']=None; db.set_state(uid,'now_playing',np)

@app.post('/api/control/connect')
def control_connect(user:dict=Depends(current_user)):
    uid=user['id']
    try:
        repair_user_storage(user,db)
        engine.ensure(uid)
        if db.get_state(uid,'playout_state','stopped')!='playing': engine.command(uid,'var.set tasia_playout = false',ensure_engine=False)
        reply=engine.connect_output(uid)
        if db.get_state(uid,'playout_state','stopped')=='playing': _resume_clock(uid)
    except Exception as exc: raise HTTPException(503,str(exc))
    return {'ok':True,'liquidsoap':reply,'shoutcast':engine.status(uid)}
@app.post('/api/control/disconnect')
def control_disconnect(user:dict=Depends(current_user)):
    uid=user['id']
    try:
        reply=engine.disconnect_output(uid)
        # Any prefetched ON DECK row belongs to the engine we just destroyed,
        # so unlock it for the next Connect.
        db.reset_reserved_queue(uid)
    except Exception as exc:
        raise HTTPException(503,str(exc))

    # Disconnect is a hard session stop, unlike Pause.  Do not leave stale
    # Now Playing/progress state claiming an old track is still resumable.
    db.set_state(uid,'playout_state','stopped')
    db.set_state(uid,'now_playing',None)
    db.set_state(uid,'playback_error',None)
    return {'ok':True,'liquidsoap':reply,'shoutcast':engine.status(uid)}
@app.post('/api/control/play')
def control_play(user:dict=Depends(current_user)):
    uid=user['id']
    try:
        repair_user_storage(user,db)
        reply=_clean(engine.command(uid,'var.set tasia_playout = true'))
        if engine.status(uid).get('output_active') is True: _resume_clock(uid)
    except Exception as exc: raise HTTPException(503,str(exc))
    db.set_state(uid,'playout_state','playing'); return {'ok':True,'liquidsoap':reply}
@app.post('/api/control/pause')
def control_pause(user:dict=Depends(current_user)):
    uid=user['id']
    try: reply=_clean(engine.command(uid,'var.set tasia_playout = false'))
    except Exception as exc: raise HTTPException(503,str(exc))
    _freeze_clock(uid); db.set_state(uid,'playout_state','paused'); return {'ok':True,'liquidsoap':reply}
@app.post('/api/control/stop')
def control_stop(user:dict=Depends(current_user)):
    uid=user['id']
    try:
        a=_clean(engine.command(uid,'var.set tasia_playout = false')); b=_clean(engine.command(uid,'scheduler.skip'))
    except Exception as exc: raise HTTPException(503,str(exc))
    db.set_state(uid,'playout_state','stopped'); db.set_state(uid,'now_playing',None); return {'ok':True,'liquidsoap':{'pause':a,'skip':b}}
@app.post('/api/skip')
def skip(user:dict=Depends(current_user)):
    try: return {'ok':True,'liquidsoap':_clean(engine.command(user['id'],'scheduler.skip'))}
    except Exception as exc: raise HTTPException(503,str(exc))


def _library_track(uid:int,track_id:int)->dict:
    t=db.library_by_id(uid,track_id)
    if not t: raise HTTPException(404,'Library track not found')
    if _local_file_path(t.get('path')) is None: raise HTTPException(404,'Audio file is missing from disk')
    return t

def _prepare_url(uid:int,body:UrlTrack):
    try: path,duration,suno_uuid=cache_remote_audio(body.url,uid)
    except Exception as exc: raise HTTPException(400,str(exc))
    tag_title,tag_artist=read_tags(path); title=(body.title or tag_title or (f'Suno {suno_uuid[:8]}' if suno_uuid else 'Remote track')).strip(); artist=(body.artist or tag_artist or '').strip()
    resolved=f'suno:{suno_uuid}' if suno_uuid else body.url
    db.upsert_library(uid,path,title,artist,duration,path.stat().st_size,folder='Suno / Remote',source_kind='suno' if suno_uuid else 'remote',remote_path=resolved)
    return path,duration,title,artist,resolved

def _source_add(body:SourceItem,user:dict,target:str):
    src=db.source_by_id(user['id'],body.source_id)
    if not src: raise HTTPException(404,'Source not found')
    try: path,duration,title,artist=download_source_audio(src,body.path,user['id'])
    except Exception as exc: raise HTTPException(400,str(exc))
    db.upsert_library(user['id'],path,title,artist,duration,path.stat().st_size,folder=source_folder_label(src,body.path),source_kind=src['kind'],source_id=src['id'],remote_path=body.path)
    source_url=f"{src['kind']}:{src['id']}:{body.path}"
    if target=='queue': item_id=db.add_queue(user['id'],path,title,artist,src['kind'],source_url,duration); return {'ok':True,'queue_id':item_id}
    item_id=db.add_playlist(user['id'],path,title,artist,src['kind'],source_url,duration); return {'ok':True,'playlist_id':item_id}


def _liq_escape(value:str)->str: return value.replace('\\','\\\\').replace('"','\\"').replace('\n',' ').replace('\r',' ')
def _annotated(user_id:int,track:dict)->str:
    import os
    source_type=str(track.get('source_type') or 'library').lower()
    parsed=catalogs.parse_path(str(track.get('path') or ''))
    if source_type in catalogs.PROVIDERS and parsed:
        provider,track_id=parsed
        try:
            uri=catalogs.stream_url(provider,db.get_catalog_settings(user_id,provider),track_id)
        except Exception as exc:
            db.set_state(user_id,'playback_error',f'{provider} stream error: {exc}')
            return ''
        fallback_title=track.get('title') or f'{provider} track'
    else:
        path=Path(track['path']).resolve()
        if not path.exists() or not path.is_file() or not os.access(path,os.R_OK): return ''
        uri=str(path)
        fallback_title=track.get('title') or path.stem
    sel=uuid.uuid4().hex; meta={'title':fallback_title,'artist':track.get('artist') or '','tasia_selection_id':sel,
      'tasia_origin':track.get('origin') or 'library','tasia_source_type':source_type,'tasia_source_url':track.get('source_url') or '',
      'tasia_queue_id':str(track.get('queue_id') or ''),'tasia_library_id':str(track.get('library_id') or ''),'tasia_duration':str(track.get('duration') or '')}
    encoded=','.join(f'{k}="{_liq_escape(str(v))}"' for k,v in meta.items()); return f'annotate:{encoded}:{uri}'

@app.get('/internal/next/{user_id}',response_class=PlainTextResponse)
def internal_next(user_id:int,key:str,request:Request):
    if request.client and request.client.host not in {'127.0.0.1','::1'}: raise HTTPException(403,'Local engine only')
    if key!=engine.INTERNAL_KEY: raise HTTPException(403,'Bad engine key')
    for _ in range(5):
        track=db.next_track(user_id)
        if not track: return ''
        line=_annotated(user_id,track)
        if line:
            db.set_state(user_id,'playback_error',None)
            return line
        if not db.get_state(user_id,'playback_error'):
            db.set_state(user_id,'playback_error',f"Audio file missing or unreadable: {track.get('path','')}")
        if track.get('queue_id'):
            try: db.mark_queue_started(user_id,int(track['queue_id']))
            except Exception: pass
    return ''
