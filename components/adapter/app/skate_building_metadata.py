"""Exact, gated lightweight-building collision assembly.

STATE1 descriptors remain diagnostic only. Scene promotion requires STATE2 with
actual transforms, native collision state, a complete representation partition,
and an independently verified executable-bound rule. This module never reads
the game or changes the imported Skate solver.
"""
import copy
import hashlib
import math
import re
from skate_authored_gate import integer, responses


def _object_path(value):
    if not isinstance(value, str):
        raise ValueError('Missing building object path')
    if "'" in value:
        value = value.split("'", 1)[1].rstrip("'")
    else:
        value = value.split(' ', 1)[-1]
    if not value.startswith('/') or '.' not in value or ':' in value:
        raise ValueError('Invalid building asset object path')
    return value


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Non-finite building transform')
    return float(value)


def _vector(value, keys):
    if not isinstance(value, dict) or any(k not in value for k in keys):
        raise ValueError('Missing exact building transform fields')
    return tuple(_number(value[k]) for k in keys)


def _transform(value):
    if not isinstance(value, dict):
        raise ValueError('Missing actual building transform')
    q = _vector(value.get('Rotation'), 'XYZW')
    p = _vector(value.get('Translation'), 'XYZ')
    s = _vector(value.get('Scale3D'), 'XYZ')
    if abs(sum(x*x for x in q) - 1) > 1e-5:
        raise ValueError('Building rotation is not normalized')
    if any(x <= 0 for x in s):
        raise ValueError('Mirrored or zero building scale needs the native matrix composition path')
    return q, p, s


def _multiply(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw*bx + ax*bw + ay*bz - az*by,
            aw*by - ax*bz + ay*bw + az*bx,
            aw*bz + ax*by - ay*bx + az*bw,
            aw*bw - ax*bx - ay*by - az*bz)


def _rotate(q, p):
    x, y, z, w = q
    px, py, pz = p
    # FQuat::RotateVector: v + 2*w*(q.xyz cross v) + q.xyz cross (2*(q.xyz cross v)).
    tx, ty, tz = 2*(y*pz-z*py), 2*(z*px-x*pz), 2*(x*py-y*px)
    return (px+w*tx+y*tz-z*ty, py+w*ty+z*tx-x*tz, pz+w*tz+x*ty-y*tx)


def compose_transform(local, piece):
    """Unreal's positive-scale FTransform local * parent field composition."""
    lq, lp, ls = _transform(local)
    pq, pp, ps = _transform(piece)
    q = _multiply(pq, lq)
    p = _rotate(pq, tuple(lp[i]*ps[i] for i in range(3)))
    return {'Rotation':dict(zip('XYZW', q)),
            'Translation':dict(zip('XYZ', (p[i]+pp[i] for i in range(3)))),
            'Scale3D':dict(zip('XYZ', (ls[i]*ps[i] for i in range(3))))}


def _lookup(values, key, label):
    if not isinstance(values, dict) or not isinstance(key, str) or not key:
        raise ValueError('Missing '+label)
    matches = [value for name, value in values.items() if isinstance(name, str) and name.casefold() == key.casefold()]
    if not matches or any(value != matches[0] for value in matches):
        raise ValueError('Unknown or ambiguous '+label+': '+key)
    return matches[0]


def _enabled(value):
    if isinstance(value, str):
        value = {'NoCollision':0, 'QueryOnly':1, 'PhysicsOnly':2,
                 'QueryAndPhysics':3, 'ProbeOnly':4, 'QueryAndProbe':5}.get(value.rsplit('::', 1)[-1])
    return integer(value, 5, 'building collision enabled')


def _channel(value, policy):
    if isinstance(value, str):
        value = _lookup(policy.get('channel_names'), value.rsplit('::', 1)[-1], 'building collision channel')
    return integer(value, 31, 'building collision channel')


def _response(value):
    if isinstance(value, str):
        value = {'ECR_Ignore':0, 'ECR_Overlap':1, 'ECR_Block':2}.get(value.rsplit('::', 1)[-1])
    return integer(value, 2, 'building collision response')


def body_collision(body, profile_override, policy):
    """Resolve the measured Player query against the native entity body.

    JgxPhysicsBodyFragment's verified profile setter calls the same engine body
    profile loader as PrimitiveComponent. A selected override replaces enabled,
    object channel and responses together: authored BuildingPreview exceptions
    must not survive BuildingPlaced. Untagged entities keep their authored body.
    """
    if not isinstance(policy, dict) or policy.get('schema') != 'S3AP1':
        raise ValueError('Verified authored collision policy is required')
    query = integer(policy.get('query_channel'), 31, 'building player query channel')
    reciprocal = responses(policy.get('query_responses'), 'building player responses')
    defaults = responses(policy.get('default_responses'), 'building channel defaults')
    if not isinstance(body, dict):
        raise ValueError('Missing authored building body')
    profile = profile_override if profile_override is not None else body.get('CollisionProfileName')
    if not isinstance(profile, str):
        raise ValueError('Missing building body profile')
    preset = None
    if profile.casefold() not in ('', 'none', 'custom'):
        preset = _lookup(policy.get('profiles'), profile, 'building collision profile')
        if not isinstance(preset, dict):
            raise ValueError('Invalid building collision profile')
    if profile_override is not None:
        if preset is None:
            raise ValueError('A native profile override requires a configured profile')
        enabled = _enabled(preset.get('enabled'))
        object_value = preset.get('object_type')
        values = responses(preset.get('responses'), 'building profile responses')
    else:
        enabled = _enabled(body.get('CollisionEnabled', (preset or {}).get('enabled')))
        object_value = body.get('ObjectType')
        if object_value is None:
            object_value = (preset or {}).get('object_type')
        values = responses(preset['responses'], 'building profile responses') if preset else defaults[:]
        if 'CollisionResponses' in body:
            serialized = body['CollisionResponses']
            if not isinstance(serialized, dict) or not isinstance(serialized.get('ResponseArray'), list):
                raise ValueError('Missing exact building response array')
            values = defaults[:]
            seen = set()
            for response in serialized['ResponseArray']:
                if not isinstance(response, dict):
                    raise ValueError('Invalid building channel response')
                channel = _channel(response.get('Channel'), policy)
                if channel in seen:
                    raise ValueError('Duplicate building channel response')
                seen.add(channel)
                # Reflected FResponseChannel's omitted Response defaults to Block.
                values[channel] = _response(response.get('Response', 2))
    if enabled not in (1, 3, 5):
        return {'included':False, 'reason':'no_query_collision', 'enabled':enabled, 'profile':profile}
    object_type = _channel(object_value, policy)
    response_player, player_response = values[query], reciprocal[object_type]
    included = response_player == 2 and player_response == 2
    return {'included':included, 'reason':'query_and_player_block' if included else 'pair_does_not_block',
            'collision_policy':'query_and_player_block', 'query_channel':query,
            'enabled':enabled, 'object_type':object_type, 'response_player':response_player,
            'player_response':player_response, 'profile':profile}


def captured_body_collision(entity, policy):
    """Use the actual native entity filter, never reconstruct it from piece flags."""
    if (not isinstance(entity, dict) or entity.get('collision_verified') is not True
            or type(entity.get('physics_present')) is not bool):
        raise ValueError('Missing verified native entity body')
    query=integer(policy.get('query_channel'),31,'building player query channel')
    reciprocal=responses(policy.get('query_responses'),'building player responses')
    if not entity['physics_present']:
        return {'included':False,'reason':'no_native_physics_body'}
    if (entity.get('dirty') is not False or type(entity.get('created')) is not bool
            or type(entity.get('actor_enabled')) is not bool):
        raise ValueError('Native building body is pending or unverified')
    collision=entity.get('collision')
    if not isinstance(collision,dict):raise ValueError('Missing actual native body filter')
    enabled=integer(collision.get('enabled'),5,'native body collision enabled')
    object_type=integer(collision.get('object_type'),31,'native body object type')
    values=responses(collision.get('responses'),'native body responses')
    if enabled and not entity['created']:raise ValueError('Native building body is not initialized')
    if enabled and not entity['actor_enabled']:raise ValueError('Native actor/body enablement is inconsistent')
    if enabled not in (1,3,5):return {'included':False,'reason':'no_query_collision'}
    if entity.get('geometry_verified') is not True:
        raise ValueError('Native building BodySetup geometry identity is unverified')
    response,player_response=values[query],reciprocal[object_type]
    included=response==2 and player_response==2
    return {'included':included,'reason':'query_and_player_block' if included else 'pair_does_not_block',
        'collision_policy':'query_and_player_block','query_channel':query,'enabled':enabled,
        'object_type':object_type,'response_player':response,'player_response':player_response,
        'profile':entity.get('profile_name','CapturedNativeBody')}


def resolve_metadata(capture, catalogue, rule, *, game_exe_sha256):
    """Return metadata only; inputs without independently verified state fail."""
    if (not isinstance(rule, dict) or rule.get('schema') != 'S3BUILDINGRULE1'
            or rule.get('verified') is not True or rule.get('game_exe_sha256') != game_exe_sha256
            or not re.fullmatch('[0-9a-f]{64}', game_exe_sha256)
            or rule.get('transform_policy') != 'entity_local_then_piece_ftransform'
            or not isinstance(rule.get('evidence'), list) or not rule['evidence']
            or any(not isinstance(s, str) or not s for s in rule['evidence'])
            or not isinstance(rule.get('selected_tag'), str) or not rule['selected_tag']):
        raise ValueError('Native building policy has not been independently verified for this executable')
    if (not isinstance(capture, dict) or capture.get('schema') != 'S3BUILDINGSTATE2'
            or capture.get('complete') is not True or capture.get('collision_verified') is not True
            or capture.get('errors') != [] or capture.get('game_exe_sha256') != game_exe_sha256):
        raise ValueError('Actual building collision flags and transforms have not been captured')
    if (not isinstance(catalogue, dict) or catalogue.get('magic') != 'S3BL1'
            or catalogue.get('schema') != 1 or catalogue.get('complete') is not True
            or catalogue.get('errors') != []):
        raise ValueError('Building catalogue is incomplete')
    settings = capture.get('settings')
    if not isinstance(settings, dict):
        raise ValueError('Missing actual building profile settings')
    for key in ('active_profile', 'inactive_profile'):
        if not isinstance(rule.get(key), str) or not rule[key] or settings.get(key) != rule[key]:
            raise ValueError('Actual building profile settings differ from the verified rule')
    lookup = {}
    for asset in capture.get('data_assets', []):
        index = asset.get('index')
        if type(index) is not int or index < 0 or index in lookup:
            raise ValueError('Invalid or duplicate runtime building data index')
        lookup[index] = _object_path(asset.get('name'))
    pieces = capture.get('pieces')
    if not isinstance(pieces, list):
        raise ValueError('Missing complete building inventory')
    result, seen = [], set()
    for piece in pieces:
        ident = piece.get('id')
        if type(ident) is not int or not 0 <= ident <= 0xffffffff or ident in seen:
            raise ValueError('Invalid or duplicate building piece ID')
        seen.add(ident)
        enabled = piece.get('collision_enabled')
        if type(enabled) is not bool:
            raise ValueError('Missing actual per-piece collision flag')
        state = piece.get('profile_state')
        actual_entities = piece.get('entities')
        actual_mode = rule.get('body_policy') == 'captured_native_entity_body_v1'
        if not actual_mode and (state not in ('active', 'inactive') or (state == 'active' and not enabled)):
            raise ValueError('Unverified or inconsistent native building profile state')
        _transform(piece.get('transform'))
        path = _object_path(piece.get('data_asset'))
        if lookup.get(piece.get('data_index')) != path:
            raise ValueError('Building runtime index does not match its actual data asset')
        data = catalogue.get('data', {}).get(path)
        if not isinstance(data, dict) or data.get('derived_available') is not True:
            raise ValueError('Building data asset has no resolved derived metadata: ' + path)
        if _object_path(piece.get('derived_asset')) != data.get('derived_path'):
            raise ValueError('Actual building derived asset differs from the authored representation')
        derived = catalogue.get('derived', {}).get(data.get('derived_path'))
        if (not isinstance(derived, dict) or derived.get('complete') is not True
                or derived.get('errors') != [] or derived.get('entity_representation_present') is not True
                or derived.get('usable_static_mesh_metadata') is not True):
            raise ValueError('Lightweight building representation is missing or unsupported: ' + path)
        entities = derived.get('entities')
        if not isinstance(entities, list) or not entities or derived.get('entity_count') != len(entities):
            raise ValueError('Building entity inventory is inconsistent')
        if type(piece.get('entity_count')) is not int or piece['entity_count'] != len(entities):
            raise ValueError('Actual building entity count differs from the authored representation')
        if actual_mode and (not isinstance(actual_entities,list) or len(actual_entities)!=len(entities)):
            raise ValueError('Complete actual entity body inventory is required')
        for index, entity in enumerate(entities):
            if (entity.get('entity_index') != index or entity.get('archetype_name') != 'StaticMeshEntity'
                    or entity.get('supported_static_mesh_metadata') is not True or entity.get('errors') != []
                    or not isinstance(entity.get('body'), dict)):
                raise ValueError('Unsupported building entity metadata')
            tags = entity.get('component_tags')
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                raise ValueError('Missing exact building entity tags')
            selected = rule['selected_tag'].casefold() in {tag.casefold() for tag in tags}
            native = actual_entities[index] if actual_mode else None
            if actual_mode:
                if (not isinstance(native,dict) or native.get('entity_index')!=index
                        or type(native.get('building_mesh')) is not bool or native['building_mesh']!=selected):
                    raise ValueError('Actual entity order or native BuildingMesh tag differs from catalogue')
                collision=native.get('collision')
                if native.get('physics_present') and isinstance(collision,dict) and collision.get('enabled') in (1,3,5):
                    if (_object_path(native.get('body_mesh'))!=_object_path(entity.get('mesh'))
                            or native.get('geometry_verified') is not True):
                        raise ValueError('Actual BodySetup mesh differs from catalogue entity geometry')
            result.append({'piece_id':ident, 'entity_index':index, 'data_asset':path,
                'mesh':_object_path(entity.get('mesh')),
                'transform':compose_transform(entity.get('transform'), piece['transform']),
                'authored_body':copy.deepcopy(entity['body']), 'component_tags':list(tags),
                'profile_override':settings[state+'_profile'] if selected and not actual_mode else None,
                'native_body':copy.deepcopy(native),
                'piece_collision_enabled':enabled, 'profile_state':state})
    return {'schema':'S3BUILDINGMETADATA1', 'complete':True, 'geometry_promoted':False,
            'piece_count':len(pieces), 'entities':result,
            'remaining_gate':'Native entity body enablement and scene promotion require integration review.'}


def promote_scene(capture, catalogue, rule, policy, *, game_exe_sha256, world_session, world):
    """Produce normal strict scene rows from a complete lightweight inventory.

    The returned package replaces only the save's lightweight building inventory.
    An empty successful inventory deliberately declares that same empty package,
    allowing removals without deleting unrelated native map/component geometry.
    Call before observed-world merging and missing-mesh export. Native S3O1 and
    world_scene already support these ordinary mesh instances and original rails.
    """
    if not isinstance(world_session, str) or not world_session or '\0' in world_session:
        raise ValueError('Trustworthy world session is required for building collision')
    if not isinstance(capture, dict) or capture.get('scope') != 'lightweight_pieces':
        raise ValueError('Complete lightweight representation scope is required')
    if not isinstance(world, str) or not world or capture.get('world') != world:
        raise ValueError('Building capture belongs to a different or unknown world')
    def ids(name):
        values = capture.get(name)
        if not isinstance(values, list):
            raise ValueError('Missing complete building '+name)
        values = [integer(v, 0xffffffff, 'building '+name) for v in values]
        if len(values) != len(set(values)):
            raise ValueError('Duplicate building '+name)
        return set(values)
    if (rule.get('body_policy')!='captured_native_entity_body_v1' or rule.get('reader_protocol')!='DWBBDY2'
            or rule.get('geometry_policy')!='actual_bodysetup_matches_catalogue_mesh'):
        raise ValueError('Verified actual native body reader policy is required for promotion')
    inventory = ids('inventory_piece_ids')
    metadata = resolve_metadata(capture, catalogue, rule, game_exe_sha256=game_exe_sha256)
    lightweight = {piece['id'] for piece in capture['pieces']}
    mode=capture.get('capture_mode','full')
    if mode not in ('full','delta'):raise ValueError('Unknown building capture mode')
    retained=ids('retained_loaded_piece_ids') if mode=='delta' or 'retained_loaded_piece_ids' in capture else set()
    native=ids('native_piece_ids') if mode=='delta' or 'native_piece_ids' in capture else lightweight
    # The accepted pre-delta full runner emitted an empty auxiliary native ID
    # list. Its actual two-pass body rows and independently recorded loaded
    # count still establish that complete native set. Never apply this to a
    # selective capture or infer unobserved bodies from the global inventory.
    reader=capture.get('native_reader')
    if (mode=='full' and not native and lightweight and not retained
            and isinstance(reader,dict) and reader.get('schema')==2
            and type(reader.get('loaded_piece_count')) is int
            and reader['loaded_piece_count']==len(lightweight)
            and reader.get('verified_passes')==2):
        native=lightweight
    if (lightweight & retained or lightweight | retained != native
            or not native <= inventory or (mode=='full' and retained)
            or ids('native_missing_piece_ids') != inventory-native):
        raise ValueError('Building native/captured/retained inventory is incomplete or overlapping')
    descriptors=capture.get('inventory')
    if not isinstance(descriptors,list):raise ValueError('Complete global building inventory descriptors are required')
    runtime={asset['index']:_object_path(asset['name']) for asset in capture['data_assets']}
    described=set();actors=set();unloaded=set()
    loaded_by_id={piece['id']:piece for piece in capture['pieces']}
    for item in descriptors:
        if not isinstance(item,dict):raise ValueError('Invalid global building descriptor')
        ident=integer(item.get('id'),0xffffffff,'global building ID')
        if ident in described:raise ValueError('Duplicate global building ID')
        described.add(ident)
        index=integer(item.get('data_index'),0xffffffff,'global building data index')
        path=runtime.get(index);data=catalogue.get('data',{}).get(path)
        if not isinstance(data,dict):raise ValueError('Global building has no catalogue binding')
        category=data.get('representation_category')
        if ident in native:
            if category!='EBuildingPieceRepresentationCategory::Lightweight':
                raise ValueError('Non-lightweight/unknown building representation must use actual actor collision')
            if ident in lightweight and loaded_by_id[ident]['data_index']!=index:raise ValueError('Loaded/global building asset identity differs')
        elif category=='EBuildingPieceRepresentationCategory::Lightweight':unloaded.add(ident)
        elif category in ('EBuildingPieceRepresentationCategory::ManagedActor','EBuildingPieceRepresentationCategory::IndividuallyReplicatedAndPersistedActor'):actors.add(ident)
        else:raise ValueError('Global building representation category is unknown')
    if described!=inventory:
        raise ValueError('Building representation inventory is incomplete or overlapping')
    for piece in capture['pieces']:
        data = catalogue['data'][_object_path(piece['data_asset'])]
        if data.get('representation_category') != 'EBuildingPieceRepresentationCategory::Lightweight':
            raise ValueError('Non-lightweight/unknown building representation must use actual actor collision')
    # Validate policy even for the meaningful empty-inventory deletion snapshot.
    if not isinstance(policy, dict) or policy.get('schema') != 'S3AP1':
        raise ValueError('Verified authored collision policy is required')
    query = integer(policy.get('query_channel'), 31, 'building player query channel')
    player_responses = responses(policy.get('query_responses'), 'building player responses')
    responses(policy.get('default_responses'), 'building channel defaults')
    managers = capture.get('managers')
    if not isinstance(managers, list) or len(managers) > 1 or(native and not managers):
        raise ValueError('Complete unambiguous building manager policy is required')
    manager_enabled = not native
    if managers:
        manager = managers[0]
        if not isinstance(manager, dict) or type(manager.get('owner_collision')) is not bool:
            raise ValueError('Actual building manager owner collision is required')
        component = manager.get('component')
        if not isinstance(component, dict):
            raise ValueError('Actual building manager component policy is required')
        enabled = integer(component.get('enabled'), 5, 'building manager collision enabled')
        object_type = integer(component.get('object_type'), 31, 'building manager object type')
        manager_responses = responses(component.get('responses'), 'building manager collision responses')
        manager_enabled = manager['owner_collision'] and enabled in (1, 3, 5) and manager_responses[query] == 2 and player_responses[object_type] == 2
    package = '/SkateRuntime/Buildings/'+hashlib.sha256(world_session.encode()).hexdigest()+'/Lightweight.Lightweight'
    objects = []
    skipped = {}
    for entity in sorted(metadata['entities'], key=lambda item:(item['piece_id'], item['entity_index'])):
        # Native actor-enable applies across every entity (including untagged
        # bodies), before the separate selected-tag profile callback.
        reason = 'manager_does_not_block' if not manager_enabled else 'piece_disabled' if not entity['piece_collision_enabled'] else None
        if reason:
            skipped[reason] = skipped.get(reason, 0)+1
            continue
        decision = captured_body_collision(entity['native_body'], policy)
        if not decision['included']:
            skipped[decision['reason']] = skipped.get(decision['reason'], 0)+1
            continue
        objects.append({
            'name':package+':Piece_'+str(entity['piece_id'])+'_Entity_'+str(entity['entity_index']),
            'mesh':entity['mesh'], 'transform':entity['transform'],
            **{key:value for key,value in decision.items() if key not in ('included', 'reason')},
            'collision_source':'verified_lightweight_building_entity',
            'building_piece_id':entity['piece_id'], 'building_entity_index':entity['entity_index']})
    return {'schema':'S3BUILDINGCOLLISION1', 'complete':True, 'packages':[package], 'objects':objects,
            'piece_count':len(lightweight), 'actor_piece_count':len(actors),
            'entity_count':len(metadata['entities']), 'included_entities':len(objects), 'skipped_entities':skipped,
            'inventory_piece_ids':sorted(inventory),'loaded_piece_ids':sorted(lightweight),
            'retained_loaded_piece_ids':sorted(retained),'native_piece_ids':sorted(native),
            'actor_piece_ids':sorted(actors),'unloaded_piece_ids':sorted(unloaded),
            'requires_retained_unloaded_merge':True,'requires_retained_loaded_merge':bool(retained)}
