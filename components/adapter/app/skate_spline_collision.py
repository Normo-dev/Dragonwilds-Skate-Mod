"""Match an unchanged live spline to its own serialized deformed BodySetup."""
import hashlib,json,math,re,struct


def _state(state,reflected=False):
    kinds=('spline_body_setup_v1','spline_body_setup_v2') if reflected else ('spline_body_setup_v1',)
    if not isinstance(state,dict) or state.get('kind') not in kinds:raise ValueError('Unknown spline collision state')
    result={'kind':'spline_reflected_parameters_v1' if reflected else state['kind']}
    mesh=state.get('mesh')
    if not isinstance(mesh,str) or not mesh.startswith('/'):raise ValueError('Spline mesh path is missing')
    result['mesh']=mesh
    for name in (('cached_mesh_body_guid',) if reflected else ('body_guid','cached_mesh_body_guid')):
        value=state.get(name)
        if not isinstance(value,str):raise ValueError('Spline body GUID is missing')
        value=value.replace('-','').upper()
        if re.fullmatch('[0-9A-F]{32}',value) is None:raise ValueError('Invalid spline body GUID')
        result[name]=value
    def f32(value):
        if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('Non-finite spline parameter')
        try:value=struct.unpack('<f',struct.pack('<f',value))[0]
        except OverflowError:raise ValueError('Spline parameter exceeds float32') from None
        if not math.isfinite(value):raise ValueError('Spline parameter exceeds float32')
        return value if value else 0.0
    for name,count in [('start_pos',3),('start_tangent',3),('start_scale',2),('start_offset',2),
                       ('end_pos',3),('end_tangent',3),('end_scale',2),('end_offset',2),('up_dir',3)]:
        value=state.get(name)
        if not isinstance(value,list) or len(value)!=count:raise ValueError('Missing spline parameter '+name)
        result[name]=[f32(x) for x in value]
    for name in ('start_roll','end_roll','boundary_min','boundary_max'):result[name]=f32(state.get(name))
    axis=state.get('forward_axis')
    if type(axis) is not int or axis not in (0,1,2):raise ValueError('Invalid spline forward axis')
    result['forward_axis']=axis
    if type(state.get('smooth')) is not bool:raise ValueError('Invalid spline interpolation flag')
    result['smooth']=state['smooth']
    return result


def state_key(state):
    return hashlib.sha256(json.dumps(_state(state),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def reflected_state_key(state):
    """Exact float32 parameters available in both owned bytes and live reflection.

    The caller must additionally match the exact component-owned BodySetup path;
    parameters alone are not permission to replace a runtime-created spline body.
    """
    return hashlib.sha256(json.dumps(_state(state,True),sort_keys=True,separators=(',',':')).encode()).hexdigest()
