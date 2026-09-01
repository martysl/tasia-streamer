from __future__ import annotations

import json
import secrets
import socket
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .control import liquidsoap_command

APP_DIR=Path(__file__).resolve().parents[1]
TEMPLATE=(APP_DIR/'liquidsoap'/'radio-user.liq.template').read_text(encoding='utf-8')
INTERNAL_KEY=secrets.token_urlsafe(24)
_lock=threading.RLock()


def _esc(v: Any) -> str:
    return str(v).replace('\\','\\\\').replace('"','\\"').replace('\n',' ').replace('\r',' ')


def ports(user_id: int) -> tuple[int,int]:
    # Practical self-hosted range; supports thousands of accounts without exposing ports in Docker.
    return 18000+user_id, 28000+user_id


@dataclass
class Engine:
    user_id: int
    process: subprocess.Popen
    control_port: int
    meta_port: int
    config_path: Path
    log_path: Path
    log_handle: Any
    settings_fingerprint: str


_engines: dict[int,Engine]={}


def _fingerprint(settings: dict) -> str:
    import hashlib
    keys=['host','port','password','sid','name','genre','url','public','bitrate','sample_rate']
    return hashlib.sha256(json.dumps({k:settings.get(k) for k in keys},sort_keys=True).encode()).hexdigest()


def render_config(user_id: int, settings: dict) -> tuple[Path,int,int]:
    control_port,meta_port=ports(user_id)
    text=TEMPLATE
    repl={
        '__USER_ID__':str(user_id),'__INTERNAL_KEY__':_esc(INTERNAL_KEY),'__CONTROL_PORT__':str(control_port),'__META_PORT__':str(meta_port),
        '__HOST__':_esc(settings['host']),'__PORT__':str(int(settings['port'])),'__PASSWORD__':_esc(settings['password']),
        '__SID__':str(int(settings.get('sid',1))),'__NAME__':_esc(settings.get('name','Tasia Radio')),'__GENRE__':_esc(settings.get('genre','')),
        '__URL__':_esc(settings.get('url','')),'__PUBLIC__':'true' if settings.get('public') else 'false',
        '__BITRATE__':str(int(settings.get('bitrate',192))),'__SAMPLE_RATE__':str(int(settings.get('sample_rate',44100))),
    }
    for k,v in repl.items(): text=text.replace(k,v)
    path=Path(f'/tmp/tasia-radio-{user_id}.liq'); path.write_text(text,encoding='utf-8')
    return path,control_port,meta_port


def _wait_port(port: int, seconds: float=8.0) -> bool:
    deadline=time.time()+seconds
    while time.time()<deadline:
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.25): return True
        except OSError: time.sleep(.15)
    return False


def _log_tail(path: Path, limit: int=5000) -> str:
    try: return path.read_text(errors='replace')[-limit:]
    except Exception: return ''


def ensure(user_id: int, force_restart: bool=False) -> Engine:
    settings=db.get_stream_settings(user_id); fp=_fingerprint(settings)
    with _lock:
        old=_engines.get(user_id)
        if old and old.process.poll() is None and old.settings_fingerprint==fp and not force_restart: return old
        if old: _stop_locked(old)
        db.reset_reserved_queue(user_id)
        path,control_port,meta_port=render_config(user_id,settings)
        check=subprocess.run(['liquidsoap','--check',str(path)],capture_output=True,text=True,timeout=30,check=False)
        if check.returncode!=0:
            raise RuntimeError('Liquidsoap config check failed:\n'+(check.stderr or check.stdout)[-4000:])
        log_path=Path(f'/tmp/tasia-radio-{user_id}.log'); log_handle=log_path.open('a',encoding='utf-8')
        proc=subprocess.Popen(['liquidsoap',str(path)],stdout=log_handle,stderr=subprocess.STDOUT,text=True)
        eng=Engine(user_id,proc,control_port,meta_port,path,log_path,log_handle,fp); _engines[user_id]=eng
        if not _wait_port(control_port):
            time.sleep(.2)
            if proc.poll() is not None:
                err=_log_tail(log_path); _stop_locked(eng); raise RuntimeError('Liquidsoap exited during startup:\n'+err)
            _stop_locked(eng); raise RuntimeError('Liquidsoap control port did not become ready')
        db.set_state(user_id,'engine_error',None)
        threading.Thread(target=_watch_metadata,args=(eng,),daemon=True,name=f'tasia-meta-{user_id}').start()
        threading.Thread(target=_watch_process,args=(eng,),daemon=True,name=f'tasia-engine-{user_id}').start()
        return eng


def _stop_locked(eng: Engine) -> None:
    try:
        if eng.process.poll() is None:
            eng.process.terminate()
            try: eng.process.wait(timeout=4)
            except subprocess.TimeoutExpired: eng.process.kill()
    except Exception: pass
    try: eng.log_handle.close()
    except Exception: pass
    _engines.pop(eng.user_id,None)


def stop_engine(user_id: int) -> None:
    with _lock:
        eng=_engines.get(user_id)
        if eng: _stop_locked(eng)


def command(user_id: int, cmd: str, ensure_engine: bool=True) -> str:
    eng=ensure(user_id) if ensure_engine else _engines.get(user_id)
    if not eng or eng.process.poll() is not None: raise OSError('Liquidsoap engine is not running')
    return liquidsoap_command(cmd,port=eng.control_port)


def _clean(raw: str) -> str:
    return '\n'.join(x.strip() for x in raw.replace('\r','').split('\n') if x.strip() and x.strip()!='END').strip()


def status(user_id: int) -> dict:
    with _lock: eng=_engines.get(user_id)
    if not eng or eng.process.poll() is not None:
        return {'engine_running':False,'output_active':False,'raw':'engine stopped','error':db.get_state(user_id,'engine_error')}
    try:
        raw=_clean(command(user_id,'shoutcast.status',ensure_engine=False)); low=raw.lower(); active=None
        if any(x in low for x in ('stopped','inactive','not started')): active=False
        elif any(x in low for x in ('started','active','running','connected')): active=True
        return {'engine_running':True,'output_active':active,'raw':raw or 'available','error':None}
    except Exception as exc:
        return {'engine_running':True,'output_active':None,'raw':str(exc),'error':None}


def connect_output(user_id: int) -> str:
    ensure(user_id); return _clean(command(user_id,'shoutcast.start',ensure_engine=False))


def disconnect_output(user_id: int) -> str:
    """Hard-disconnect this user's radio output.

    `shoutcast.stop` is requested first for a clean protocol shutdown, then the
    per-user Liquidsoap process is terminated as the final guarantee that the
    SHOUTcast source socket is physically closed.  Connect will create a fresh
    engine later.

    This is intentionally different from Pause: Pause keeps the engine/output
    alive and feeds silence, while Disconnect tears the connection down.
    """
    with _lock:
        eng=_engines.get(user_id)
    if not eng or eng.process.poll() is not None:
        return 'already disconnected'

    replies=[]
    # Silence first so the encoder does not keep advancing the scheduler while
    # the network output is being closed.  Failure here must not prevent the
    # hard disconnect below.
    try:
        replies.append(_clean(command(user_id,'var.set tasia_playout = false',ensure_engine=False)))
    except Exception as exc:
        replies.append(f'pause command: {exc}')
    try:
        replies.append(_clean(command(user_id,'shoutcast.stop',ensure_engine=False)))
    except Exception as exc:
        replies.append(f'shoutcast.stop: {exc}')

    # output.stop is normally enough, but the UI button is called Disconnect:
    # stopping the per-user engine guarantees the TCP source socket is gone.
    time.sleep(.15)
    stop_engine(user_id)
    return '\n'.join(x for x in replies if x) or 'disconnected'


def restart(user_id: int, reconnect: bool=False) -> None:
    stop_engine(user_id); ensure(user_id,force_restart=True)
    if reconnect: connect_output(user_id)


def _meta(port: int) -> dict|None:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/internal-meta',timeout=.7) as r: data=json.loads(r.read().decode())
        if isinstance(data,dict): return data
        if isinstance(data,list): return {str(k):v for k,v in data if isinstance(k,str)}
    except Exception: pass
    return None


def _ival(v):
    try: return int(v) if v not in (None,'') else None
    except Exception: return None


def _fval(v):
    try: return float(v) if v not in (None,'') else None
    except Exception: return None


def _watch_metadata(eng: Engine) -> None:
    last=None
    while eng.process.poll() is None:
        meta=_meta(eng.meta_port); sel=str(meta.get('tasia_selection_id') or '') if meta else ''
        if meta and sel and sel!=last:
            qid=_ival(meta.get('tasia_queue_id')); lid=_ival(meta.get('tasia_library_id'))
            if qid is not None: db.mark_queue_started(eng.user_id,qid)
            if lid is not None: db.mark_library_played(eng.user_id,lid)
            now=time.time(); payload={'title':meta.get('title') or 'Untitled','artist':meta.get('artist') or '',
              'source_type':meta.get('tasia_source_type') or 'library','source_url':meta.get('tasia_source_url') or '', 'origin':meta.get('tasia_origin') or 'library',
              'queue_id':qid,'library_id':lid,'selection_id':sel,'filename':meta.get('filename') or meta.get('initial_uri') or '',
              'duration':_fval(meta.get('tasia_duration')),'started_at':datetime.now(timezone.utc).isoformat(),'started_at_epoch':now,
              'paused_total':0.0,'paused_at_epoch':None}
            db.set_state(eng.user_id,'now_playing',payload); last=sel
        time.sleep(.8)


def _watch_process(eng: Engine) -> None:
    code=eng.process.wait()
    with _lock:
        if _engines.get(eng.user_id) is eng: _engines.pop(eng.user_id,None)
    try: eng.log_handle.flush()
    except Exception: pass
    if code != 0: db.set_state(eng.user_id,'engine_error',_log_tail(eng.log_path))


def shutdown_all() -> None:
    with _lock:
        for eng in list(_engines.values()): _stop_locked(eng)


def autostart_users() -> None:
    for user in db.list_users():
        try:
            s=db.get_stream_settings(user['id'])
            if s.get('auto_start'):
                ensure(user['id']); db.set_state(user['id'],'playout_state','playing'); connect_output(user['id'])
        except Exception as exc:
            db.set_state(user['id'],'engine_error',str(exc))
