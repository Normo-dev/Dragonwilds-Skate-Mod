"""Verify real cache-worker report persistence using private, tiny geometry.

Exercises success, an actual incomplete geometry result, and early identity
rejection. No IPC, worker simulation, game process or controller is opened.
"""
from pathlib import Path
from contextlib import redirect_stdout,redirect_stderr
from unittest.mock import patch
import hashlib,io,json,struct,sys,uuid

WORK=Path(__file__).resolve().parent


def run():
    import skate_cache_worker as worker
    import skate_map_cache as cache

    ident=uuid.uuid4().hex
    private=WORK/f'cache-report-checks-{ident}'
    private.mkdir()
    assert private.resolve().is_relative_to(WORK)
    data=private/'dragonwilds-map-data'
    mailbox=private/'skate-mailbox'
    geometry=data/'static/geometry'
    built=data/'prepared'
    for directory in [mailbox,geometry,built]:directory.mkdir(parents=True,exist_ok=True)

    floor='/Game/ReportCheck/Floor.Floor'
    unsupported='/Game/ReportCheck/UnresolvedCollider.UnresolvedCollider'
    def save_mesh(name,value):
        key=hashlib.sha256(name.encode()).hexdigest().upper()[:24]
        (geometry/f'{key}.json').write_text(json.dumps({'path':name,**value}),encoding='utf-8')
    save_mesh(floor,{'trace':'CTF_UseSimpleAndComplex','agg':{'BoxElems':[
        {'X':600.,'Y':600.,'Z':100.,'Center':{'X':0.,'Y':0.,'Z':-50.},'Rotation':{'Pitch':0.,'Yaw':0.,'Roll':0.}}
    ]}})
    save_mesh(unsupported,{'trace':'CTF_UseDefault','agg':{},'unhandled_geometry':{'LevelSetElems':1}})
    transform={'Translation':{'X':0.,'Y':0.,'Z':0.},'Rotation':{'X':0.,'Y':0.,'Z':0.,'W':1.},'Scale3D':{'X':1.,'Y':1.,'Z':1.}}
    def item(mesh,suffix):
        return {'name':f'StaticMeshComponent /Game/ReportCheck.World:PersistentLevel.{suffix}.Mesh',
                'mesh':'StaticMesh '+mesh,'enabled':3,'transform':transform}

    generation='report-check-'+ident
    def scene(revision,objects,actual_generation=generation):
        name=f'scene-{generation}-{revision}.json'
        (mailbox/name).write_text(json.dumps({'generation':actual_generation,'revision':revision,
            'objects':objects,'packages':['/Game/ReportCheck.World'],'errors':[],
            'physicsSettings':{'engine_default':1}}),encoding='utf-8')
        return name
    good=scene(3,[item(floor,'Floor')])
    incomplete=scene(4,[item(floor,'Floor'),item(unsupported,'UnresolvedCollider')])
    mismatch=scene(5,[item(floor,'Floor')],actual_generation='other-generation')
    def descriptor(revision,scene_revision,name):
        return {'generation':generation,'revision':revision,'scene_revision':scene_revision,
                'scene_file':name,'anchor':[100.,-200.,50.],'center':[0.,0.,0.],
                'radius_cm':5000.,'heading':.25}
    requests=[descriptor(7,3,good),descriptor(8,4,incomplete),descriptor(9,5,mismatch),descriptor(10,3,good)]

    Original=cache.MapCache
    class FixtureCache(Original):
        def __init__(self):super().__init__(root=data)
    output,errors=io.StringIO(),io.StringIO()
    with patch.object(worker,'WORK',private),patch.object(worker,'MAILBOX',mailbox),patch.object(worker,'BUILT',built),\
         patch.object(cache,'ROOT',data),patch.object(cache,'MapCache',FixtureCache),\
         patch.object(sys,'stdin',io.StringIO(''.join(json.dumps(r)+'\n' for r in requests))),\
         redirect_stdout(output),redirect_stderr(errors):
        worker.run()
    responses=[json.loads(line) for line in output.getvalue().splitlines()]
    assert len(responses)==4
    assert [r['ok'] for r in responses]==[True,False,False,True]

    reports={}
    for i in [0,1,3]:
        request=requests[i]
        path=built/f'{generation}-{request["revision"]}.report.json'
        assert path.is_file(),f'Report missing for revision {request["revision"]}'
        report=json.loads(path.read_text())
        expected={**request,'mode':'persistent'}
        assert report['descriptor']==expected
        assert report['report']['anchor']==request['anchor'] and report['report']['center']==request['center']
        assert report['report']['complete']==(i!=1)
        triangle_path=path.with_name(f'{generation}-{request["revision"]}.triangles')
        magic,count=struct.unpack('<4sI',triangle_path.read_bytes()[:8])
        assert magic==b'S3T1' and count==12 and triangle_path.stat().st_size==8+36*count
        reports[request['revision']]=report
    assert reports[7]['report']==responses[0]['report']
    assert reports[10]['report']==responses[3]['report']
    failure=responses[1]
    assert Path(failure['report_file'])==built/f'{generation}-8.report.json'
    assert reports[8]['report']['unsupported_shapes']==1
    assert reports[8]['report']['unresolved_empty_geometry_instances']==1
    assert unsupported in failure['error'],failure
    assert 'Collision coverage is incomplete' in failure['error']
    assert 'does not match the request' in responses[2]['error']
    assert 'report_file' not in responses[2]
    assert not (built/f'{generation}-9.report.json').exists()
    assert not list(built.glob('*.tmp'))
    assert len(list(built.glob('*.report.json')))==3

    summary={'directory':str(private),'generation':generation,
        'cases':['successful report persisted','incomplete full report persisted before rejection',
                 'early identity rejection does not reuse previous report path','success after failure'],
        'revisions':{str(r):{'scene_revision':v['descriptor']['scene_revision'],
                          'complete':v['report']['complete'],'triangles':v['report']['total']} for r,v in reports.items()},
        'responses':responses}
    (private/'results.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (private/'diagnostic.log').write_text(errors.getvalue(),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':run()
