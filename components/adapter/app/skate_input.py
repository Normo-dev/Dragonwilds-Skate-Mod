"""Host input conversion only: send the existing simulation its Xbox-state ABI.

SDL's gamepad database handles physical PlayStation, Xbox and other recognised
controllers. No virtual controller driver, game memory input hook, or global
keyboard listener is used. Keyboard state comes from the game's Lua adapter.
"""
import ctypes as C
from pathlib import Path
import time
import json
import os

BUTTONS={0:0x1000,1:0x2000,2:0x4000,3:0x8000,4:0x20,6:0x10,
         7:0x40,8:0x80,9:0x100,10:0x200,11:1,12:2,13:4,14:8}
CAMERA_MODES=('high','low','dragonwilds')


def next_camera_mode(mode):
    return CAMERA_MODES[(CAMERA_MODES.index(mode)+1)%len(CAMERA_MODES)]


def source_camera_mode(mode):
    """The third host view uses the original high camera's movement following."""
    if mode not in CAMERA_MODES:raise ValueError('Invalid camera mode')
    return 'high' if mode=='dragonwilds' else mode

def neutral():
    return {'buttons':0,'triggers':[0,0],'left':[0,0],'right':[0,0]}

def neg16(value):
    return max(-32768,min(32767,-int(value)))

def sdl_pad(axes,buttons):
    if len(axes)!=6:raise ValueError('Expected six SDL gamepad axes')
    return {'buttons':sum(mask for key,mask in BUTTONS.items() if key in buttons),
            'left':[int(axes[0]),neg16(axes[1])],
            'right':[int(axes[2]),neg16(axes[3])],
            'triggers':[round(max(0,min(32767,int(v)))*255/32767) for v in axes[4:]]}

def checked_pad(value):
    if not isinstance(value,dict):return neutral()
    try:
        result={}
        buttons=value['buttons']
        if type(buttons)!=int or not 0<=buttons<=65535:raise ValueError()
        result['buttons']=buttons
        for key,minimum,maximum in [('left',-32768,32767),('right',-32768,32767),('triggers',0,255)]:
            pair=value[key]
            if not isinstance(pair,list) or len(pair)!=2 or any(type(v)!=int or not minimum<=v<=maximum for v in pair):
                raise ValueError()
            result[key]=pair.copy()
        return result
    except (KeyError,TypeError,ValueError):return neutral()

def merge_pad(controller,keyboard,mirror_steering=True):
    controller=checked_pad(controller);keyboard=checked_pad(keyboard)
    result={'buttons':controller['buttons']|keyboard['buttons'],
            'triggers':[max(a,b) for a,b in zip(controller['triggers'],keyboard['triggers'])]}
    for key in ('left','right'):
        # A keyboard stick gesture owns its entire stick, preventing controller
        # drift from turning a requested straight flick into a diagonal trick.
        result[key]=(keyboard[key] if any(keyboard[key]) else controller[key]).copy()
    if mirror_steering:result['left'][0]=neg16(result['left'][0])
    return result

class Gamepads:
    """SDL API calls remain on the relay's main thread; this creates no window."""
    def __init__(self,path):
        self.dll=C.CDLL(str(Path(path).resolve()))
        self.handle=None;self.name=None;self.error=None;self.next_scan=0
        signatures={
          'SDL_Init':([C.c_uint32],C.c_bool),'SDL_Quit':([],None),
          'SDL_SetHint':([C.c_char_p,C.c_char_p],C.c_bool),
          'SDL_GetError':([],C.c_char_p),'SDL_UpdateGamepads':([],None),
          'SDL_PumpEvents':([],None),'SDL_FlushEvents':([C.c_uint32,C.c_uint32],None),
          'SDL_GetGamepads':([C.POINTER(C.c_int)],C.POINTER(C.c_uint32)),
          'SDL_free':([C.c_void_p],None),'SDL_OpenGamepad':([C.c_uint32],C.c_void_p),
          'SDL_CloseGamepad':([C.c_void_p],None),'SDL_GamepadConnected':([C.c_void_p],C.c_bool),
          'SDL_GetGamepadName':([C.c_void_p],C.c_char_p),
          'SDL_GetGamepadVendorForID':([C.c_uint32],C.c_uint16),
          'SDL_GetGamepadAxis':([C.c_void_p,C.c_int],C.c_int16),
          'SDL_GetGamepadButton':([C.c_void_p,C.c_int],C.c_bool),
          'SDL_SetGamepadEventsEnabled':([C.c_bool],None),
        }
        for name,(args,result) in signatures.items():
            fn=getattr(self.dll,name);fn.argtypes=args;fn.restype=result
        # The helper has no game window. Actual input is separately gated to the
        # configured Dragonwilds process and its in-game UI/paused state.
        for key in ('SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS','SDL_JOYSTICK_HIDAPI_PS4','SDL_JOYSTICK_HIDAPI_PS5'):
            self.dll.SDL_SetHint(key.encode(),b'1')
        if not self.dll.SDL_Init(0x2000):
            error=self.dll.SDL_GetError()
            self.dll.SDL_Quit()
            raise RuntimeError('SDL gamepad initialization: '+(error or b'unknown error').decode(errors='replace'))
        self.dll.SDL_SetGamepadEventsEnabled(False)

    def sample(self):
        d=self.dll;d.SDL_PumpEvents();d.SDL_UpdateGamepads();d.SDL_FlushEvents(0,0xffff)
        if self.handle and not d.SDL_GamepadConnected(self.handle):
            d.SDL_CloseGamepad(self.handle);self.handle=None;self.name=None;self.next_scan=0
        now=time.monotonic()
        if not self.handle and now>=self.next_scan:
            self.next_scan=now+1
            count=C.c_int();ids=d.SDL_GetGamepads(C.byref(count))
            try:
                candidates=[int(ids[i]) for i in range(count.value)] if ids else []
                # Prefer a directly visible Sony controller over its duplicate
                # DS4Windows virtual pad. Once selected, keep it until unplugged.
                candidates.sort(key=lambda key:d.SDL_GetGamepadVendorForID(key)!=0x054c)
                for key in candidates:
                    self.handle=d.SDL_OpenGamepad(key)
                    if self.handle:
                        self.name=(d.SDL_GetGamepadName(self.handle) or b'Gamepad').decode(errors='replace')
                        break
            finally:
                if ids:d.SDL_free(ids)
        if not self.handle:return neutral()
        axes=[d.SDL_GetGamepadAxis(self.handle,i) for i in range(6)]
        buttons={i for i in BUTTONS if d.SDL_GetGamepadButton(self.handle,i)}
        return sdl_pad(axes,buttons)

    def close(self):
        if self.handle:self.dll.SDL_CloseGamepad(self.handle);self.handle=None
        self.dll.SDL_Quit()

class ForegroundGame:
    """Check only foreground process identity; never collect keyboard events."""
    def __init__(self,game_root):
        self.expected=(Path(game_root)/'RSDragonwilds/Binaries/Win64/RSDragonwilds-Win64-Shipping.exe').resolve()
        self.user=C.WinDLL('user32',use_last_error=True)
        self.kernel=C.WinDLL('kernel32',use_last_error=True)
        self.user.GetForegroundWindow.argtypes=[];self.user.GetForegroundWindow.restype=C.c_void_p
        self.user.GetWindowThreadProcessId.argtypes=[C.c_void_p,C.POINTER(C.c_uint32)]
        self.user.GetWindowThreadProcessId.restype=C.c_uint32
        self.kernel.OpenProcess.argtypes=[C.c_uint32,C.c_bool,C.c_uint32];self.kernel.OpenProcess.restype=C.c_void_p
        self.kernel.QueryFullProcessImageNameW.argtypes=[C.c_void_p,C.c_uint32,C.c_wchar_p,C.POINTER(C.c_uint32)]
        self.kernel.QueryFullProcessImageNameW.restype=C.c_bool
        self.kernel.CloseHandle.argtypes=[C.c_void_p];self.kernel.CloseHandle.restype=C.c_bool
        self.last=None;self.allowed=False;self.expires=0

    def active(self):
        window=self.user.GetForegroundWindow();now=time.monotonic()
        if window==self.last and now<self.expires:return self.allowed
        self.last=window;self.expires=now+.5;self.allowed=False
        if not window:return False
        pid=C.c_uint32();self.user.GetWindowThreadProcessId(window,C.byref(pid))
        handle=self.kernel.OpenProcess(0x1000,False,pid.value)
        if not handle:return False
        try:
            size=C.c_uint32(32768);buffer=C.create_unicode_buffer(size.value)
            if self.kernel.QueryFullProcessImageNameW(handle,0,buffer,C.byref(size)):
                self.allowed=Path(buffer.value).resolve()==self.expected
        finally:self.kernel.CloseHandle(handle)
        return self.allowed

class CameraToggle:
    def __init__(self):self.down=False;self.keyboard_serial=None
    def sample(self,pad,keyboard_serial,allowed):
        down=bool(pad['buttons']&0x80)
        keyboard_changed=(self.keyboard_serial is not None and keyboard_serial!=self.keyboard_serial)
        changed=allowed and ((down and not self.down) or keyboard_changed)
        self.down=down;self.keyboard_serial=keyboard_serial
        # Reserve R3 for the camera; never pass that host action to skating.
        pad['buttons']&=~0x80
        return changed

class InputRouter:
    def __init__(self,root):
        from runtime_paths import load_paths
        paths=load_paths(root)
        if 'game_root' not in paths:raise ValueError('Configure the Dragonwilds installation before starting input')
        self.pads=Gamepads(paths['gamepad_library'])
        self.foreground=ForegroundGame(paths['game_root']);self.toggle=CameraToggle()
        self.settings_path=paths['mailbox']/'user-settings.json'
        self.camera='high';self.save_error=None
        if self.settings_path.exists():
            value=json.loads(self.settings_path.read_text(encoding='utf-8'))
            if not isinstance(value,dict) or value.get('camera','high') not in CAMERA_MODES:
                raise ValueError('Invalid camera preference in user-settings.json')
            self.camera=value.get('camera','high')

    def sample(self,host,active):
        pad=self.pads.sample();focused=self.foreground.active()
        allowed=bool(active and focused and host.get('input_allowed',True))
        changed=self.toggle.sample(pad,host.get('camera_serial',0),allowed)
        if changed:
            self.camera=next_camera_mode(self.camera)
            temp=self.settings_path.with_suffix('.tmp')
            try:
                temp.write_text(json.dumps({'schema':1,'camera':self.camera}),encoding='utf-8')
                os.replace(temp,self.settings_path);self.save_error=None
            except OSError as error:self.save_error=str(error)
        return {'controls':merge_pad(pad,host.get('controls')) if allowed else neutral(),
                'focused':focused,'allowed':allowed,'camera_changed':changed,'camera_mode':self.camera,
                'controller_connected':bool(self.pads.handle),'controller_name':self.pads.name}

    def close(self):self.pads.close()
