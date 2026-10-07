"""Build exact player collision policy from owned INI files and read-only CDO data."""
from pathlib import Path
import argparse,hashlib,json,re

RESPONSES={'ECR_Ignore':0,'ECR_Overlap':1,'ECR_Block':2}
ENABLED={'NoCollision':0,'QueryOnly':1,'PhysicsOnly':2,'QueryAndPhysics':3,'ProbeOnly':4,'QueryAndProbe':5}
NATIVE=['WorldStatic','WorldDynamic','Pawn','Visibility','Camera','PhysicsBody','Vehicle','Destructible']
SECTION='/script/engine.collisionprofile'


def number(value,maximum,label,enums=None):
    if isinstance(value,str) and enums is not None:
        value=enums.get(value.split('::')[-1],value)
    if type(value) is not int or not 0<=value<=maximum:raise ValueError('Invalid '+label+': '+str(value))
    return value


def split_top(text):
    result=[];start=depth=0;quoted=escaped=False
    for index,char in enumerate(text):
        if escaped:escaped=False;continue
        if char=='\\' and quoted:escaped=True;continue
        if char=='"':quoted=not quoted
        elif not quoted:
            if char=='(':depth+=1
            elif char==')':
                depth-=1
                if depth<0:raise ValueError('Unbalanced INI struct')
            elif char==',' and depth==0:result.append(text[start:index].strip());start=index+1
    if quoted or depth:raise ValueError('Unterminated INI struct')
    result.append(text[start:].strip());return result


def value(text):
    text=text.strip()
    if text.startswith('"'):return json.loads(text)
    if text.startswith('('):
        if not text.endswith(')'):raise ValueError('Invalid INI tuple')
        inside=text[1:-1].strip()
        if not inside:return []
        fields=split_top(inside)
        if fields[0].startswith('('):return [value(v) for v in fields]
        result={}
        for field in fields:
            if '=' not in field:raise ValueError('Missing INI field assignment')
            key,entry=field.split('=',1)
            if key.strip() in result:raise ValueError('Duplicate INI struct key')
            result[key.strip()]=value(entry)
        return result
    if text in ('True','False'):return text=='True'
    if re.fullmatch(r'-?\d+',text):return int(text)
    return text


def config_arrays(configs):
    arrays={'DefaultChannelResponses':[],'EditProfiles':[]}
    for text in configs:
        section=''
        for raw in text.splitlines():
            line=raw.strip()
            if not line or line.startswith((';','#')):continue
            if line.startswith('['):section=line[1:-1].casefold();continue
            if section!=SECTION or '=' not in line:continue
            key,payload=line.split('=',1);operation=key[0] if key[:1] in '+-.!' else '='
            key=key[1:] if operation!='=' else key
            if key not in arrays:continue
            if operation=='!':arrays[key]=[];continue
            entry=value(payload)
            if not isinstance(entry,dict):raise ValueError('Invalid '+key+' record')
            if operation=='-':arrays[key]=[old for old in arrays[key] if old!=entry]
            elif operation=='=':arrays[key]=[entry]
            elif operation=='.' or entry not in arrays[key]:arrays[key].append(entry)
    return arrays


def response_vector(entries,label):
    if not isinstance(entries,list) or len(entries)!=32:raise ValueError(label+' requires all32 measured responses')
    result=[None]*32
    for entry in entries:
        index=number(entry.get('channel'),31,label+' channel')
        if result[index] is not None:raise ValueError('Duplicate '+label+' channel')
        result[index]=number(entry.get('response'),2,label+' response',RESPONSES)
    if any(v is None for v in result):raise ValueError('Missing '+label+' response')
    return result


def generate(probe,config_texts,config_hashes):
    collision=probe.get('collision')
    if not isinstance(collision,dict):raise ValueError('Missing read-only collision probe section')
    arrays=config_arrays(config_texts)
    aliases={name:index for index,name in enumerate(NATIVE)}
    aliases.update({f'EngineTraceChannel{i}':7+i for i in range(1,7)})
    aliases.update({f'GameTraceChannel{i}':13+i for i in range(1,19)})
    aliases.update({'ECC_'+name:index for name,index in list(aliases.items())})
    lookup={name.casefold():index for name,index in aliases.items()}
    def channel(name):
        if type(name) is int:return number(name,31,'collision channel')
        if not isinstance(name,str):raise ValueError('Missing collision channel')
        short=name.split('::')[-1];index=lookup.get(short.casefold())
        if index is None:raise ValueError('Unknown collision channel: '+name)
        # Preserve observed casing too for consumers reading serialized FNames.
        aliases[short]=index
        return index
    configured={};declared={}
    for entry in arrays['DefaultChannelResponses']:
        index=channel(entry.get('Channel'));name=entry.get('Name')
        if not isinstance(name,str) or not name:raise ValueError('Missing custom channel name')
        response=number(entry.get('DefaultResponse'),2,'configured channel response',RESPONSES)
        if type(entry.get('bTraceType')) is not bool:raise ValueError('Missing channel trace/object classification')
        if index in declared and declared[index]!=name.casefold():raise ValueError('Conflicting custom channel definitions')
        prior=lookup.get(name.casefold())
        if prior is not None and prior!=index:raise ValueError('Ambiguous collision channel alias')
        declared[index]=name.casefold();configured[index]=response;aliases[name]=index;lookup[name.casefold()]=index

    measured={}
    for name,entry in collision.get('components',{}).items():
        profile=entry.get('profile')
        if not isinstance(profile,str):raise ValueError('Missing component profile')
        measured['/Script/'+name.removeprefix('/Script/')]=dict(
            enabled=number(entry.get('enabled'),5,'component enabled',ENABLED),profile=profile,
            object_type=channel(entry.get('object_type')),responses=response_vector(entry.get('responses'),'component'))
    if not measured:raise ValueError('No measured component defaults')
    raw_profiles={}
    for entry in collision.get('profiles',[]):
        name=entry.get('name')
        if not isinstance(name,str) or not name or name in raw_profiles:raise ValueError('Invalid/duplicate collision profile')
        raw_profiles[name]=entry
    if not raw_profiles:raise ValueError('No measured collision profiles')

    # p.CustomResponses contains pre-edit definitions. UE applies EditProfiles
    # separately; these must also be applied before comparing with live CDOs.
    edits={}
    for entry in arrays['EditProfiles']:
        name=entry.get('Name')
        if name not in raw_profiles:raise ValueError('EditProfiles references an unmeasured profile: '+str(name))
        responses=entry.get('CustomResponses')
        if responses=='':responses=[]
        if not isinstance(responses,list):raise ValueError('Invalid EditProfiles response array')
        edits.setdefault(name,[]).extend(responses)
    def overrides(name):
        result={}
        for entry in raw_profiles[name].get('responses',[]):
            result[channel(entry.get('channel'))]=number(entry.get('response'),2,'profile response',RESPONSES)
        for entry in edits.get(name,[]):
            # UE's omitted FResponseChannel.Response is Block. Require live
            # profile/CDO agreement below before accepting this serialization.
            result[channel(entry.get('Channel'))]=number(entry.get('Response','ECR_Block'),2,'edited response',RESPONSES)
        return result
    if 'BlockAllDynamic' not in raw_profiles:raise ValueError('BlockAllDynamic evidence is unavailable')
    changes=overrides('BlockAllDynamic')
    witnesses=[entry for entry in measured.values() if entry['profile']=='BlockAllDynamic']
    if not witnesses:raise ValueError('No measured BlockAllDynamic component witness')
    defaults=[None]*32
    for index in range(32):
        if index in configured:defaults[index]=configured[index]
        elif index not in changes:
            responses={entry['responses'][index] for entry in witnesses}
            if len(responses)!=1:raise ValueError('Conflicting measured default response')
            defaults[index]=responses.pop()
        else:raise ValueError('Cannot establish default for overridden unconfigured channel '+str(index))
    if defaults[:8]!=[2]*8:raise ValueError('Measured native BlockAll defaults do not establish the expected baseline')
    profiles={}
    for name,entry in raw_profiles.items():
        responses=list(defaults)
        for index,response in overrides(name).items():responses[index]=response
        object_name=entry.get('object_type')
        if object_name is None:raise ValueError('Missing profile object type field')
        profiles[name]={'enabled':number(entry.get('enabled'),5,'profile enabled',ENABLED),
                        'object_type':None if object_name in ('','None') else channel(object_name),'responses':responses}
    for native,entry in measured.items():
        preset=profiles.get(entry['profile'])
        if preset is None:raise ValueError('Unmeasured CDO profile '+entry['profile'])
        if any(preset[key]!=entry[key] for key in ('enabled','object_type','responses')):
            different=[i for i,(a,b) in enumerate(zip(preset['responses'],entry['responses'])) if a!=b]
            raise ValueError('Reconstructed profile differs from '+native+' at channels '+str(different))
    pawn=collision.get('pawn_capsule',{})
    query=channel(pawn.get('object_type'));query_responses=response_vector(pawn.get('responses'),'player capsule')
    if number(pawn.get('enabled'),5,'capsule enabled',ENABLED) not in (1,3,5):raise ValueError('Player capsule has no query collision')
    preset=profiles.get(pawn.get('profile'))
    if preset is None or preset['object_type']!=query or preset['responses']!=query_responses:
        raise ValueError('Player capsule does not match its reconstructed collision profile')
    actor=collision.get('actor',{}).get('enabled')
    if type(actor) is not bool:raise ValueError('Actor collision default is unavailable')
    sanitized={'components':measured,'profiles':profiles,'pawn':{'profile':pawn['profile'],'object_type':query,
                'responses':query_responses,'enabled':pawn['enabled']},'actor_default_collision':actor}
    digest=hashlib.sha256(json.dumps(sanitized,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'schema':'S3AP1','query_channel':query,'query_responses':query_responses,
            'channel_names':dict(sorted(aliases.items())),'default_responses':defaults,
            'components':measured,'profiles':profiles,'actor_default_collision':actor,
            'source':{'generator_schema':1,'probe_schema':probe.get('schema'),'collision_sha256':digest,
                      'config_sha256':config_hashes,'default_evidence':'measured_unoverridden_BlockAllDynamic_and_owned_channel_defaults',
                      'validated_component_count':len(measured)}}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--probe',type=Path,required=True)
    parser.add_argument('--config-dir',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();texts=[];hashes={}
    for name in ('BaseEngine.ini','DefaultEngine.ini'):
        data=(args.config_dir/name).read_bytes();hashes[name]=hashlib.sha256(data).hexdigest();texts.append(data.decode('utf-8-sig'))
    policy=generate(json.loads(args.probe.read_text(encoding='utf-8-sig')),texts,hashes)
    args.output.parent.mkdir(parents=True,exist_ok=True);temporary=args.output.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(policy,sort_keys=True,separators=(',',':')),encoding='utf-8');temporary.replace(args.output)
    print(json.dumps({'query_channel':policy['query_channel'],'profiles':len(policy['profiles']),
                      'components':len(policy['components']),'source':policy['source']}))


if __name__=='__main__':main()
