"""Decode bounded read-only native building packets after typed Lua capture.

This decoder adds no geometry or policy inference. It checks the exact native
protocol and the typed asset bindings captured on the game thread. The caller's
independent release/supplement policy gate still owns promotion authorization.
"""
from pathlib import Path
import copy,hashlib,math,re,struct

ROW=struct.Struct('<IQQ10d4BI')
ENTITY=struct.Struct('<IQ8B32BQQQIQQ')
HEADER=struct.Struct('<7sIQQI')

def integer(value,maximum,label):
    if type(value)is not int or not 0<=value<=maximum:raise ValueError('Invalid building packet '+label)
    return value

def pointer(value,label):
    return integer(value,9007199254740991,label)

def boolean(value):
    if value not in (0,1):raise ValueError('Invalid building packet flag')
    return bool(value)

def bindings(values,label,fields):
    if not isinstance(values,list):raise ValueError('Missing typed building '+label+' bindings')
    result={}
    for value in values:
        if not isinstance(value,dict):raise ValueError('Invalid typed building '+label)
        address=pointer(value.get('address'),label+' address')
        if not address or address in result:raise ValueError('Duplicate or absent typed building '+label+' pointer')
        for field in fields:
            if not isinstance(value.get(field),str)or not value[field]:raise ValueError('Missing typed building '+label+' name')
        result[address]=value
    return result

def decode_state_packets(capture,root):
    if not isinstance(capture,dict)or capture.get('schema')!='S3BUILDINGSTATE2' or capture.get('complete')is not True or capture.get('collision_verified')is not True:
        raise ValueError('Building packets require a completed verified STATE2 capture')
    record=capture.get('piece_packets')
    if not isinstance(record,dict)or record.get('schema')!='S3BUILDINGPACKETS1':raise ValueError('Invalid building packet descriptor')
    if capture.get('pieces')!=[]:raise ValueError('Building packet capture also supplied conflicting expanded pieces')
    relative=record.get('path')
    if not isinstance(relative,str)or re.fullmatch(r'building-packets-[0-9]+-[0-9]+-[0-9]+\.bin',relative)is None:
        raise ValueError('Building packet file must be a generated mailbox basename')
    root=Path(root).resolve();path=root/relative
    if path.is_symlink()or path.resolve()!=path or(hasattr(path,'is_junction')and path.is_junction()):raise ValueError('Linked building packet file rejected')
    size=integer(record.get('bytes'),67108864,'byte count')
    count=integer(record.get('packet_count'),50000,'packet count');piece_count=integer(record.get('piece_count'),50000,'piece count')
    before=path.stat()
    if before.st_size!=size or size<6:raise ValueError('Building packet size differs from its captured descriptor')
    raw=path.read_bytes();after=path.stat()
    if (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
        raise ValueError('Building packet file changed while reading')
    if len(raw)!=size or raw[:6]!=b'S3BPK1':raise ValueError('Malformed building packet stream')
    reader=capture.get('native_reader',{})
    if reader.get('schema')!=2 or reader.get('verified_passes')!=2:raise ValueError('Missing verified native body capture passes')
    manager=pointer(reader.get('manager_address'),'manager');mass=pointer(reader.get('mass_address'),'Mass subsystem')
    if not manager or not mass:raise ValueError('Missing native manager identity')
    assets=bindings(capture.get('data_assets'),'data asset',('name',))
    derived=bindings(capture.get('derived_assets'),'derived asset',('name',))
    bodies=bindings(capture.get('body_bindings'),'BodySetup',('mesh','body'))
    pieces=[];seen=set();offset=6;packets=0
    while offset<len(raw):
        if len(raw)-offset<4:raise ValueError('Truncated building packet length')
        length=struct.unpack_from('<I',raw,offset)[0];offset+=4
        if length<HEADER.size or length>400000 or length>len(raw)-offset:raise ValueError('Invalid framed building packet length')
        data=memoryview(raw)[offset:offset+length];offset+=length;packets+=1
        magic,version,ma,sa,n=HEADER.unpack_from(data)
        if magic!=b'DWBBDY2' or version!=1 or ma!=manager or sa!=mass or not 1<=n<=4:
            raise ValueError('Building packet identity/version/count mismatch')
        pos=HEADER.size
        for _ in range(n):
            if len(data)-pos<ROW.size:raise ValueError('Truncated native building row')
            values=ROW.unpack_from(data,pos);pos+=ROW.size
            ident,asset,deriv,*rest=values
            if ident in seen or len(seen)>=50000:raise ValueError('Duplicate or excessive native building piece')
            seen.add(ident)
            if asset not in assets or deriv not in derived:raise ValueError('Native building pointer lacks a typed asset binding')
            transforms=rest[:10]
            if any(not math.isfinite(v)for v in transforms):raise ValueError('Non-finite native building transform')
            q=transforms[:4];xyz=transforms[4:7];scale=transforms[7:10]
            if abs(sum(v*v for v in q)-1)>0.0001 or min(scale)<=0:raise ValueError('Invalid native building rotation/scale')
            preview,ghost,defer,enabled=map(boolean,rest[10:14]);nentities=integer(rest[14],1024,'entity count')
            row={'id':ident,'data_asset_address':asset,'derived_asset_address':deriv,
                'data_asset':assets[asset]['name'],'data_index':integer(assets[asset].get('index'),2147483647,'runtime data index'),
                'derived_asset':derived[deriv]['name'],
                'transform':{'Rotation':dict(zip('XYZW',q)),'Translation':dict(zip('XYZ',xyz)),'Scale3D':dict(zip('XYZ',scale))},
                'preview':preview,'ghosted':ghost,'can_defer_collision_update':defer,'collision_enabled':enabled,
                'entity_count':nentities,'entities':[]}
            if len(data)-pos<nentities*ENTITY.size:raise ValueError('Truncated native building entities')
            for index in range(nentities):
                fields=ENTITY.unpack_from(data,pos);pos+=ENTITY.size
                ei,handle=fields[:2];present,tag,actor,created,dirty=map(boolean,fields[2:7]);desired,effective,channel=fields[7:10]
                responses=list(fields[10:42]);profile,body,owner,body_index,resolved,external=fields[42:]
                if ei!=index or dirty:raise ValueError('Native building entity order/dirty state rejected')
                integer(desired,5,'desired collision');integer(effective,5,'effective collision');integer(channel,31,'object channel')
                for response in responses:integer(response,2,'channel response')
                for ptr in (body,owner,resolved,external):pointer(ptr,'body identity')
                entity={'entity_index':index,'handle':str(handle),'physics_present':present,'building_mesh':tag,
                    'actor_enabled':actor,'created':created,'dirty':False,'desired_enabled':desired,
                    'collision':{'enabled':effective,'object_type':channel,'responses':responses},'profile_name_bits':str(profile),
                    'body_setup_address':body,'source_object_address':owner,'body_index':body_index,
                    'resolved_owner_address':resolved,'resolved_external_address':external,'collision_verified':True}
                if present:
                    if owner!=manager or body_index!=ident:raise ValueError('Native building body lacks its source identity')
                    if effective in (1,3,5):
                        if body not in bodies:raise ValueError('Native query body lacks its typed geometry identity')
                        entity.update(body_mesh=bodies[body]['mesh'],body_setup_asset=bodies[body]['body'],geometry_verified=True)
                row['entities'].append(entity)
            pieces.append(row)
        if pos!=len(data):raise ValueError('Trailing native building packet data')
    if packets!=count or len(pieces)!=piece_count or reader.get('loaded_piece_count')!=piece_count:
        raise ValueError('Building packet census differs from the completed native capture')
    result=copy.deepcopy(capture);result['pieces']=pieces
    result['piece_packets']={**record,'sha256':hashlib.sha256(raw).hexdigest()}
    return result
