"""Pinned lightweight-building promotion and durable observation provenance.

The release/setup caller supplies verified pins. This module never derives a
trusted game identity from a scene's own claims and never changes the base map.
"""
from pathlib import Path
import copy,hashlib,json,re
from skate_map_cache import package_path
from skate_building_metadata import promote_scene
from skate_authored_gate import validate_scene_policy

PREFIX='/SkateRuntime/Buildings/'

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()

def digest(value):return hashlib.sha256(canonical(value)).hexdigest()

def sha(value,label):
    if not isinstance(value,str) or re.fullmatch('[0-9a-fA-F]{64}',value) is None:
        raise ValueError('Missing verified '+label+' SHA256 pin')
    return value.lower()

class BuildingSceneGate:
    def __init__(self,*,catalogue_path,rule_path,game_exe_sha256,catalogue_sha256,rule_sha256,
                 policy,supplement_base_fingerprint,mapping_path,supplement_mapping_sha256,packet_root=None):
        self.game_sha=sha(game_exe_sha256,'game executable')
        self.base_sha=sha(supplement_base_fingerprint,'base collision')
        self.policy=copy.deepcopy(policy)
        self.packet_root=Path(packet_root) if packet_root is not None else None
        self.files=[(Path(catalogue_path),sha(catalogue_sha256,'building catalogue')),
                    (Path(rule_path),sha(rule_sha256,'building rule')),
                    (Path(mapping_path),sha(supplement_mapping_sha256,'owned mapping'))]
        self._verify_files()
        self.catalogue=json.loads(self.files[0][0].read_bytes())
        self.rule=json.loads(self.files[1][0].read_bytes())
        if (self.rule.get('schema')!='S3BUILDINGRULE1' or self.rule.get('verified') is not True
                or self.rule.get('game_exe_sha256')!=self.game_sha):
            raise ValueError('Building rule is not verified for the independently pinned game')
        if (self.catalogue.get('magic')!='S3BL1' or self.catalogue.get('schema')!=1
                or self.catalogue.get('complete') is not True or self.catalogue.get('errors')!=[]
                or self.catalogue.get('mapping_sha256')!=self.files[2][1]):
            raise ValueError('Building catalogue does not match the verified mapping supplement')
        self.binding={'schema':1,'game_exe_sha256':self.game_sha,'base_fingerprint':self.base_sha,
            'catalogue_sha256':self.files[0][1],'rule_sha256':self.files[1][1],
            'mapping_sha256':self.files[2][1],'policy_sha256':digest(self.policy)}

    def _verify_files(self):
        for path,expected in self.files:
            with path.open('rb')as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
            if actual!=expected:raise ValueError('Pinned building input changed: '+path.name)

    def prepare(self,request,scene,base_fingerprint,*,observed=None):
        self._verify_files()
        if base_fingerprint!=self.base_sha:raise ValueError('Building supplement belongs to a different base collision map')
        validate_scene_policy(scene,self.policy)
        if not isinstance(scene.get('packages'),list) or not isinstance(scene.get('objects'),list):
            raise ValueError('Building promotion needs an explicit scene inventory')
        if any(package_path(p).startswith(PREFIX) for p in scene['packages']):
            raise ValueError('Incoming scene claims a reserved building package')
        if any(package_path(o['name']).startswith(PREFIX) for o in scene['objects']):
            raise ValueError('Incoming scene claims a reserved building object')
        capture=scene.get('building_state')
        if capture is None:
            if scene.get('building_inventory')!='retain_verified':
                raise ValueError('Fresh verified building inventory or explicit retain_verified declaration is required')
            return copy.deepcopy(scene),None
        if capture.get('piece_packets') is not None:
            if self.packet_root is None:raise ValueError('A verified private mailbox root is required for building packets')
            from skate_building_packets import decode_state_packets
            capture=decode_state_packets(capture,self.packet_root)
        if scene.get('building_inventory') not in (None,'replace_verified'):
            raise ValueError('Building inventory declaration contradicts its new snapshot')
        if capture.get('world_session')!=request.get('world_session'):
            raise ValueError('Building capture belongs to a different or unknown save session')
        promoted=promote_scene(capture,self.catalogue,self.rule,self.policy,
            game_exe_sha256=self.game_sha,world_session=request['world_session'],world=scene.get('world'))
        if promoted.get('complete') is not True or len(promoted.get('packages',[]))!=1:
            raise ValueError('Building promotion did not produce one complete authoritative inventory')
        package=promoted['packages'][0]
        if not package.startswith(PREFIX):raise ValueError('Building promoter returned an unreserved package')
        # Native pieces disappear when their area streams out. The complete
        # replicated global ID inventory distinguishes that from deletion. Only
        # earlier verified rows may survive unloading; unseen pieces get no
        # invented geometry and are counted explicitly in coverage reporting.
        previous=None;prior_objects=[];prior_stamp=None
        if observed is not None:
            prior_merged,prior_pending=observed.preview(scene,request['world_session'],base_fingerprint)
            previous=prior_pending[2].get('building_collision_evidence');prior_stamp=prior_pending[3]
            if previous is not None:
                self.validate_observed(request,prior_merged,prior_pending,None)
                prior_objects=[o for o in prior_merged['objects'] if package_path(o['name'])==package]
            elif any(p.startswith(PREFIX) for p in prior_merged['packages']):
                raise ValueError('Unverified prior building rows cannot be retained')
        runtime={a['index']:a['name'].split(' ',1)[-1] for a in capture['data_assets']}
        descriptor_values={str(item['id']):{'asset':runtime[item['data_index']],
            **{k:item[k] for k in ('location','yaw','ghosted') if k in item}} for item in capture['inventory']}
        descriptors={key:digest(value) for key,value in descriptor_values.items()}
        manager_values=[{k:v for k,v in manager.items() if k!='name' and k!='component'}|
            {'component':{k:v for k,v in manager['component'].items() if k!='name'}} for manager in capture['managers']]
        manager_policy=digest(manager_values)
        loaded=set(promoted['loaded_piece_ids']);unloaded=set(promoted['unloaded_piece_ids'])
        retained_loaded=set(promoted.get('retained_loaded_piece_ids',[]));retained=set();invalidated=set()
        if previous is not None:
            old_descriptors=previous.get('piece_descriptors',{})
            known=set(previous.get('verified_piece_ids',[]))
            for ident in unloaded & known:
                if (previous.get('manager_policy_sha256')==manager_policy and
                        old_descriptors.get(str(ident))==descriptors.get(str(ident))):retained.add(ident)
                else:invalidated.add(ident)
            for ident in retained_loaded:
                if (ident not in known or previous.get('manager_policy_sha256')!=manager_policy or
                        old_descriptors.get(str(ident))!=descriptors.get(str(ident))):
                    raise ValueError('Retained loaded building lacks matching prior native evidence; perform a full building capture')
        elif retained_loaded:raise ValueError('Initial building delta has no prior native evidence; perform a full building capture')
        retained|=retained_loaded
        objects=promoted['objects']+[o for o in prior_objects if o.get('building_piece_id') in retained]
        objects.sort(key=lambda o:(o['building_piece_id'],o['building_entity_index']))
        verified=loaded|retained
        prepared=copy.deepcopy(scene)
        prepared['packages'].append(package);prepared['objects'].extend(objects)
        record={'binding':self.binding,'world_session':request['world_session'],'world':scene['world'],
            'package':package,'objects_sha256':digest(objects),
            'piece_count':len(verified),'included_entities':len(objects),
            'loaded_piece_count':len(loaded),'retained_loaded_piece_count':len(retained_loaded),
            'retained_unloaded_piece_count':len(retained-retained_loaded),
            'unseen_unloaded_piece_count':len(unloaded-verified),'invalidated_unloaded_piece_count':len(invalidated),
            'verified_piece_ids':sorted(verified),'piece_descriptors':{str(i):descriptors[str(i)] for i in sorted(verified)},
            'piece_descriptor_values':{str(i):descriptor_values[str(i)] for i in sorted(verified)},
            'native_piece_ids':sorted(loaded|retained_loaded),'manager_policy_values':manager_values,'manager_policy_sha256':manager_policy,
            'generation':scene.get('generation'),'revision':scene.get('revision')}
        record['record_sha256']=digest(record)
        if observed is not None:record['_expected_observed_stamp']=prior_stamp
        return prepared,record

    def validate_observed(self,request,merged,pending,fresh_record):
        """Validate retained rows or stage fresh evidence in the same commit.

        ObservedWorldState's existing expected-file-stamp check makes the row and
        evidence publication one transaction; no parallel marker can get ahead.
        """
        key,path,state,stamp,changed=pending
        previous=state.get('building_collision_evidence')
        record=fresh_record if fresh_record is not None else previous
        if not isinstance(record,dict):raise ValueError('No previously verified building inventory is available')
        if digest({k:v for k,v in record.items() if k!='record_sha256' and not k.startswith('_')})!=record.get('record_sha256'):
            raise ValueError('Building collision evidence integrity check failed')
        if (record.get('binding')!=self.binding or record.get('world_session')!=request['world_session']
                or record.get('world')!=merged.get('world')):
            raise ValueError('Retained building inventory provenance does not match this map/save/rule')
        package=record.get('package')
        if not isinstance(package,str) or not package.startswith(PREFIX):
            raise ValueError('Retained building inventory package is invalid')
        actual_packages={p for p in merged['packages'] if p.startswith(PREFIX)}
        if actual_packages!={package}:raise ValueError('Retained building inventory is missing or has extra packages')
        objects=[o for o in merged['objects'] if package_path(o['name'])==package]
        if digest(objects)!=record.get('objects_sha256'):
            raise ValueError('Retained building collision rows do not match their verified inventory')
        if fresh_record is not None:
            if '_expected_observed_stamp' in fresh_record and fresh_record['_expected_observed_stamp']!=stamp:
                raise ValueError('Building observations changed during unloaded-piece retention; retry')
            saved={k:v for k,v in fresh_record.items() if not k.startswith('_')}
            state['building_collision_evidence']=copy.deepcopy(saved)
            changed=changed or previous!=saved
        report={'status':'fresh_verified' if fresh_record is not None else 'retained_verified',
            'package':package,'piece_count':record['piece_count'],'included_entities':record['included_entities'],
            'captured_generation':record.get('generation'),'captured_revision':record.get('revision')}
        for field in ('loaded_piece_count','retained_loaded_piece_count','retained_unloaded_piece_count','unseen_unloaded_piece_count','invalidated_unloaded_piece_count'):
            report[field]=record.get(field,0)
        report['coverage']='verified_loaded_and_previously_observed_buildings'
        return (key,path,state,stamp,changed),report

    def publish_baseline(self,state,mailbox):
        """A planning hint only; later deltas still validate durable evidence."""
        record=state['building_collision_evidence']
        value={'schema':'S3BUILDINGBASELINE1','world':record['world'],'world_session':record['world_session'],
            'game_exe_sha256':self.game_sha,'rule_sha256':self.binding['rule_sha256'],
            'manager_policy':record['manager_policy_values'],'descriptors':record['piece_descriptor_values'],
            'native_piece_ids':record['native_piece_ids']}
        value['identifier']=digest(value)
        native=set(value['native_piece_ids'])
        header={k:v for k,v in value.items() if k not in ('descriptors','native_piece_ids')}
        encoded=canonical(header)+b'\n'+b''.join(canonical({'id':int(ident),'native':int(ident)in native,'descriptor':descriptor})+b'\n'
            for ident,descriptor in sorted(value['descriptors'].items(),key=lambda item:int(item[0])))
        root=Path(mailbox).resolve();root.mkdir(parents=True,exist_ok=True);path=root/'building-baseline.json'
        if path.is_symlink()or path.resolve()!=path:raise ValueError('Linked building baseline rejected')
        if path.exists() and json.loads(path.read_bytes()).get('schema')!='S3BUILDINGBASELINE1':
            raise ValueError('Refusing to overwrite an unowned building baseline')
        temporary=path.with_suffix('.json.tmp')
        if temporary.is_symlink()or temporary.resolve()!=temporary:raise ValueError('Linked building baseline temporary rejected')
        raw=canonical(value)
        if not path.exists()or path.read_bytes()!=raw:temporary.write_bytes(raw);temporary.replace(path)
        rows=root/('building-baseline-'+value['identifier']+'.rows');rows_tmp=rows.with_suffix('.rows.tmp')
        if any(p.is_symlink()or p.resolve()!=p for p in(rows,rows_tmp)):raise ValueError('Linked building baseline rows rejected')
        if rows.exists():
            if rows.read_bytes()!=encoded:raise ValueError('Immutable building baseline rows changed')
        else:rows_tmp.write_bytes(encoded);rows_tmp.replace(rows)
        ident=root/'building-baseline.id';ident_tmp=root/'building-baseline.id.tmp'
        if any(p.is_symlink()or p.resolve()!=p for p in(ident,ident_tmp)):raise ValueError('Linked building baseline identifier rejected')
        marker=value['identifier'].encode()+b'\n'
        if not ident.exists()or ident.read_bytes()!=marker:ident_tmp.write_bytes(marker);ident_tmp.replace(ident)
