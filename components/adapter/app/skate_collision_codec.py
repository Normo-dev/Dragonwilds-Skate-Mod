import struct,json,math,re
HEADER=struct.Struct('<4sIHI3d')
def decode(blob):
    if blob[:4]==b'S3C2':
        if len(blob)>8192:raise ValueError('Map descriptor exceeds limit')
        value=json.loads(blob[4:])
        if not isinstance(value,dict):raise ValueError('Invalid map descriptor')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',value.get('generation','')):raise ValueError('Invalid generation')
        revision=value.get('revision')
        if type(revision)!=int or not 1<=revision<=0xffffffff:raise ValueError('Invalid revision')
        scene_revision=value.get('scene_revision',revision)
        if type(scene_revision)!=int or not 1<=scene_revision<=revision:raise ValueError('Invalid scene revision')
        value['scene_revision']=scene_revision
        for key in ['anchor','center']:
            point=value.get(key)
            if not isinstance(point,list) or len(point)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>1e8 for v in point):
                raise ValueError('Invalid '+key)
        if type(value.get('heading')) not in (int,float) or not math.isfinite(value['heading']):raise ValueError('Invalid heading')
        mode=value.get('mode','persistent')
        if mode not in ('persistent','whole_world'):raise ValueError('Invalid collision mode')
        if mode=='whole_world' and (not isinstance(value.get('world_session'),str) or not 1<=len(value['world_session'])<=512 or any(ord(c)<32 for c in value['world_session'])):
            raise ValueError('Whole-world collision requires a stable world_session identity')
        if mode=='persistent' and (type(value.get('radius_cm')) not in (int,float) or not 5000<=value['radius_cm']<=100000):raise ValueError('Invalid coverage radius')
        if not re.fullmatch(r'scene-[a-zA-Z0-9_-]+\.json',value.get('scene_file','')):raise ValueError('Invalid scene filename')
        value['mode']=mode;return value
    if len(blob)<HEADER.size: raise ValueError('Truncated collision header')
    magic,revision,ng,nt,heading,cx,cz=HEADER.unpack_from(blob)
    if magic!=b'S3C1' or ng>128 or not 0<nt<=100000: raise ValueError('Invalid collision header')
    start=HEADER.size+ng
    if len(blob)!=start+nt*36: raise ValueError('Collision size mismatch')
    generation=blob[HEADER.size:start].decode('utf-8')
    triangles=[[list(v[:3]),list(v[3:6]),list(v[6:])] for v in struct.iter_unpack('<9f',blob[start:])]
    return {'generation':generation,'revision':revision,'triangles':triangles,'rails':[],
            'heading':heading,'center':[cx,cz]}
