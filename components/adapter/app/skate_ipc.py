"""Local Windows mappings shared with the Lua transport helper."""
import ctypes,mmap,struct
from pathlib import Path
from runtime_paths import load_paths

class Channel:
    def __init__(self,name,capacity,prefix='Local\\DragonwildsSkate.v3.'):
        self.memory=mmap.mmap(-1,capacity,tagname=prefix+name,access=mmap.ACCESS_WRITE)
        self.capacity=capacity
        self.sequence=ctypes.c_longlong.from_buffer(self.memory,8)
        self.channel=['Frame','Host','Status','Collision'].index(name)
        self.library=ctypes.CDLL(str(load_paths()['transport_library']))
        self.publish=self.library.ipc_publish
        self.publish.argtypes=[ctypes.c_uint32,ctypes.c_char_p,ctypes.c_size_t]
        self.publish.restype=ctypes.c_int
    def write(self,payload):
        if len(payload)>self.capacity-64: raise ValueError('Shared packet exceeds mapping')
        if self.publish(self.channel,payload,len(payload))!=1: raise RuntimeError('Cannot publish shared packet')
    def read(self):
        for _ in range(2):
            seq=self.sequence.value
            if seq<=0 or seq&1: continue
            length=struct.unpack_from('<I',self.memory,16)[0]
            if length>self.capacity-64: return None
            data=self.memory[64:64+length]
            if self.sequence.value==seq: return data
        return None
    def close(self):
        del self.sequence
        self.memory.close()

class Transport:
    def __init__(self,prefix='Local\\DragonwildsSkate.v3.'):
        self.frame=Channel('Frame',65536,prefix)
        self.host=Channel('Host',8192,prefix)
        self.status=Channel('Status',8192,prefix)
        self.collision=Channel('Collision',4*1024*1024,prefix)
    def close(self):
        for channel in [self.frame,self.host,self.status,self.collision]: channel.close()
