"""Native execution of the same host appearance mapping, with no physics."""
import ctypes as C
from pathlib import Path
from runtime_paths import load_paths
import numpy as np

class Bone(C.Structure):
    _fields_=[('parent',C.c_int32),('source',C.c_int32),('local',C.c_double*16),
              ('fit',C.c_double*9),('scale',C.c_double*3)]

class NativeRender:
    def __init__(self,renderer,names):
        from skate_host_render import MAPPING
        self.names=tuple(names)
        source={n:i for i,n in enumerate(names)}
        host={b['name']:i for i,b in enumerate(renderer.rig)}
        assert host['Root']==0 and host['Pelvis']==1
        self.library=C.CDLL(str(load_paths()['render_library']))
        init=self.library.rig_init
        init.argtypes=[C.POINTER(Bone),C.c_size_t,C.POINTER(C.c_int32),C.POINTER(C.c_int32),C.c_size_t];init.restype=C.c_int32
        self.call=self.library.rig_pose
        self.call.argtypes=[C.POINTER(C.c_double),C.c_size_t,C.POINTER(C.c_double),C.POINTER(C.c_double),C.c_size_t]
        self.call.restype=C.c_int32
        bones=(Bone*len(renderer.rig))()
        for i,b in enumerate(renderer.rig):
            n=b['name'];v=bones[i]
            v.parent=host.get(b['parent'],-1)
            v.source=source[MAPPING[n][0]] if n in MAPPING else -1
            v.local[:]=renderer.local[n].T.reshape(-1).tolist()
            v.fit[:]=renderer.fits.get(n,np.eye(3)).T.reshape(-1).tolist()
            v.scale[:]=np.linalg.norm(renderer.bind[n][:3,:3],axis=0).tolist()
        legs=(C.c_int32*10)(*[host[n] for n in ['Thigh_L','calf_l','foot_l']],source['RIGHTLEG'],source['RIGHTFOOT'],
                           *[host[n] for n in ['Thigh_R','calf_r','foot_r']],source['LEFTLEG'],source['LEFTFOOT'])
        board=(C.c_int32*len(renderer.board_names))(*[source[n] for n in renderer.board_names])
        assert init(bones,len(bones),legs,board,len(board))==1,'Native rig initialization failed'
        self.input=(C.c_double*(len(names)*16))()
        self.root=(C.c_double*16)()
        self.output=(C.c_double*((len(bones)+len(board)+1)*10))()
        self.body_len=len(bones)*10
    def pose(self,response):
        if tuple(response['names'])!=self.names: raise ValueError('Source skeleton changed')
        self.input[:]=[v for bone in response['bones'] for v in bone]
        self.root[:]=response['root']
        if self.call(self.input,len(self.names),self.root,self.output,len(self.output))!=1:
            raise ValueError('Native appearance fitting failed')
        values=list(self.output)
        return values[:self.body_len],values[self.body_len:-10],values[-10:]
