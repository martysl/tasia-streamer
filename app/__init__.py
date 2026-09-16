from .spotify_free_fix import install as _install_spotify_free_fix
from .suno_public_fix import install as _install_suno_public_fix
from . import tasia_talk as _tasia_talk
from .tasia_talk import install as _install_tasia_talk
from .tasia_talk_streammesh import install as _install_tasia_streammesh
from . import tasia_talk_secure as _tasia_talk_secure  # registers secure POST polling route

_install_spotify_free_fix()
_install_suno_public_fix()
_install_tasia_talk()
_install_tasia_streammesh()

# Some of Tasia's AI backends can need ~40 seconds. The Liquidsoap prefetch
# request now allows up to 55 seconds, so let Tasia Talk wait 45 seconds before
# skipping a late interstitial instead of using the module's conservative 18s.
_tasia_talk.take_prepared.__defaults__ = (45.0,)
