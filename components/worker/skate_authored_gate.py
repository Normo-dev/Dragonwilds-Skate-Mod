"""Validate the exporter evidence for authored collision before indexing it."""
from pathlib import Path
import hashlib,json


def integer(value,maximum,label):
    if type(value) is not int or not 0<=value<=maximum:
        raise ValueError('Invalid '+label)
    return value


def responses(value,label):
    if not isinstance(value,list) or len(value)!=32:raise ValueError('Invalid '+label)
    return [integer(v,2,label) for v in value]


class AuthoredGate:
    def __init__(self,root):
        self.root=Path(root);path=self.root/'authored-policy.json'
        raw=path.read_bytes();self.policy=json.loads(raw)
        if self.policy.get('schema')!='S3AP1':raise ValueError('Unsupported authored collision policy')
        integer(self.policy.get('query_channel'),31,'authored query channel')
        responses(self.policy.get('query_responses'),'authored query responses')
        self.fingerprint=hashlib.sha256(raw).hexdigest().upper()
        packages=json.loads((self.root/'authored-packages.json').read_text())
        if not isinstance(packages,list) or not packages or any(not isinstance(p,str) or not p.startswith('/') or '.' in p or ':' in p for p in packages):
            raise ValueError('Invalid authored package inventory')
        if len(packages)!=len(set(packages)):raise ValueError('Duplicate authored package inventory entry')
        self.sidecars={};sidecar_digest=hashlib.sha256();total=0
        for package in sorted(packages):
            key=hashlib.sha256(package.encode()).hexdigest().upper()[:16]
            name=key+'.instances.jsonl';path=self.root/'static'/(key+'.policy.json')
            raw=path.read_bytes();side=json.loads(raw);sidecar_digest.update(raw)
            if (side.get('schema')!='S3AP1' or side.get('package')!=package or side.get('policy_fingerprint')!=self.fingerprint or
                side.get('complete') is not True or side.get('errors')!=[] or side.get('transform_policy')!='archetype_per_field_v1'):
                raise ValueError('Incomplete or mismatched authored collision evidence: '+package)
            for field in ('considered','included_components','instances'):
                integer(side.get(field),2**63-1,'authored '+field)
            if side['included_components']>side['considered'] or not isinstance(side.get('reasons'),dict):
                raise ValueError('Invalid authored component census: '+package)
            for reason,count in side['reasons'].items():integer(count,2**63-1,'authored reason count')
            if sum(side['reasons'].values())!=side['considered'] or side['reasons'].get('query_and_player_block',0)!=side['included_components']:
                raise ValueError('Authored component reason census mismatch: '+package)
            if not (self.root/'static'/name).is_file():raise ValueError('Missing authored instance file: '+package)
            self.sidecars[name]=side;total+=side['instances']
        actual={p.name for p in (self.root/'static').glob('*.instances.jsonl')}
        expected=set(self.sidecars)
        if actual!=expected:raise ValueError('Authored instance inventory mismatch: '+str(sorted(actual^expected)[:8]))
        actual={p.name for p in (self.root/'static').glob('*.policy.json')}
        expected={n.replace('.instances.jsonl','.policy.json') for n in self.sidecars}
        if actual!=expected:raise ValueError('Authored sidecar inventory mismatch: '+str(sorted(actual^expected)[:8]))
        self.summary={'schema':'S3AP1','complete':True,'policy_fingerprint':self.fingerprint,
                      'package_count':len(packages),'instance_count':total,
                      'sidecars_sha256':sidecar_digest.hexdigest(),'query_channel':self.policy['query_channel'],
                      'query_responses':self.policy['query_responses'],'transform_policy':'archetype_per_field_v1'}

    def row(self,path,row):
        side=self.sidecars[path.name];collision=row.get('collision') or {}
        name=row.get('name','');name=name.split("'",1)[1].rstrip("'") if "'" in name else name.split(' ',1)[-1]
        if not name.split(':',1)[0].startswith(side['package']+'.'):
            raise ValueError('Authored row belongs to another package: '+name)
        if (row.get('schema')!=2 or row.get('collision_policy')!='query_and_player_block' or
            row.get('policy_fingerprint')!=self.fingerprint or collision.get('included') is not True or
            collision.get('reason')!='query_and_player_block'):
            raise ValueError('Authored row collision evidence is missing or mismatched: '+name)
        enabled=integer(collision.get('enabled'),5,'authored component collision enabled')
        object_type=integer(collision.get('object_type'),31,'authored component object type')
        response=integer(collision.get('response_to_player'),2,'authored component player response')
        reciprocal=integer(collision.get('player_response'),2,'authored player response')
        if (enabled not in (1,3,5) or response!=2 or reciprocal!=2 or
            self.policy['query_responses'][object_type]!=2):
            raise ValueError('Authored row does not block the measured player query: '+name)
        if not isinstance(collision.get('profile'),str) or not str(collision.get('native_class','')).startswith('/Script/'):
            raise ValueError('Authored row has no resolved collision class/profile: '+name)

    def count(self,path,count):
        if count!=self.sidecars[path.name]['instances']:
            raise ValueError('Authored row count mismatch: '+path.name)


def validate_scene_policy(scene,policy):
    """A saved/live overlay must use the same measured player query as its base."""
    value=scene.get('query_policy')
    if not isinstance(value,dict):raise ValueError('Live player query policy is missing')
    channel=integer(value.get('channel'),31,'live query channel')
    enabled=integer(value.get('enabled'),5,'live player collision enabled')
    values=responses(value.get('responses'),'live query responses')
    if channel!=policy['query_channel'] or values!=policy['query_responses'] or enabled not in (1,3,5):
        raise ValueError('Live player query policy differs from authored collision policy')


def live_included(item,policy=None):
    # Legacy numeric fixtures can omit evidence; production authored mode cannot.
    enabled=item.get('enabled')
    disabled=(0,2,4,'ECollisionEnabled::NoCollision','ECollisionEnabled::PhysicsOnly','ECollisionEnabled::ProbeOnly')
    if enabled in disabled or item.get('actor_enabled') is False or item.get('owner_collision') is False:return False
    if policy is not None:
        enabled=integer(enabled,5,'live component collision enabled')
        channel=integer(item.get('query_channel'),31,'live component query channel')
        object_type=integer(item.get('object_type'),31,'live component object type')
        response=integer(item.get('response_player'),2,'live component player response')
        reciprocal=integer(item.get('player_response'),2,'live reciprocal response')
        if channel!=policy['query_channel'] or item.get('collision_policy')!='query_and_player_block':
            raise ValueError('Live component collision policy is missing or mismatched')
        if reciprocal!=policy['query_responses'][object_type]:raise ValueError('Live reciprocal response differs from measured policy')
        return enabled in (1,3,5) and response==2 and reciprocal==2
    keys=('response_player','player_response') if 'response_player' in item else ('response_pawn','player_response')
    return not any(item.get(key) in (0,1,'ECR_Ignore','ECR_Overlap') for key in keys)
