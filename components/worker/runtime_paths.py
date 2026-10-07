"""Local installation paths. Published settings contain no developer paths."""
from pathlib import Path
import json

BASE=Path(__file__).resolve().parent
DEFAULTS={
    'mailbox':'skate-mailbox',
    'assets':'skate3-converted/assets',
    'map_data':'dragonwilds-map-data',
    'worker':'skate-build/debug/deps/dragonwilds_skate_worker-persistent5.exe',
    'transport_library':'skate-ipc-build-stream/release/skate_ipc.dll',
    'lua_transport_library':'skate-ipc-build-clock/release/skate_ipc.dll',
    'render_library':'skate-render-build/release/skate_render.dll',
    'map_export':'map-export-build/DragonwildsMapExport.dll',
    'dotnet':'dotnet10/dotnet.exe',
    'gamepad_library':'sdl3/SDL3.dll',
}

def load_paths(root=BASE):
    root=Path(root).resolve()
    settings=root/'runtime.json'
    values={}
    if settings.exists():
        document=json.loads(settings.read_text(encoding='utf-8-sig'))
        if not isinstance(document,dict) or document.get('schema')!=1:
            raise ValueError('runtime.json must be a schema 1 object')
        values=document.get('paths',{})
        if not isinstance(values,dict) or set(values)-set(DEFAULTS)-{'game_root'}:
            raise ValueError('Unknown installation path in runtime.json')
    result={}
    for key,value in {**DEFAULTS,**values}.items():
        if not isinstance(value,str) or not value.strip() or '\0' in value:
            raise ValueError(f'Invalid installation path: {key}')
        path=Path(value)
        result[key]=(path if path.is_absolute() else root/path).resolve()
    result['root']=root
    return result

def game_paks(root=BASE):
    game=load_paths(root).get('game_root')
    if game is None:
        raise ValueError('Dragonwilds install is not configured. Run the mod setup first.')
    paks=game/'RSDragonwilds/Content/Paks'
    if not paks.is_dir():
        raise ValueError('Configured Dragonwilds installation has no RSDragonwilds/Content/Paks folder')
    return paks
