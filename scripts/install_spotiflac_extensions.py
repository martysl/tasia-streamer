from pathlib import Path

from SpotiFLAC.extensions import manager as ext_manager

REGISTRY = (
    "https://raw.githubusercontent.com/spotiflacapp/"
    "SpotiFLAC-Extension/main/registry.json"
)
EXT_DIR = Path("/app/spotiflac-extensions")
SERVICES = ("tidal-web", "qobuz-web", "amazon", "deezer")

EXT_DIR.mkdir(parents=True, exist_ok=True)
ext_manager.DEFAULT_EXT_DIR = EXT_DIR
manager = ext_manager.ExtensionManager(
    ext_dir=EXT_DIR,
    auto_install_downloads=False,
)
for ext_id in SERVICES:
    print(f"Installing SpotiFLAC extension: {ext_id}")
    manager.install(ext_id, registry_url=REGISTRY)

print("SpotiFLAC lossless extensions ready:", ", ".join(SERVICES))
