#[path="../src/compact_world.rs"] mod compact_world;
#[path="../src/compact_geometry.rs"] mod compact_geometry;
#[path="../src/compact_rails.rs"] mod compact_rails;
#[path="../../skate3-mashup/crates/render_anim/src/skate/rails.rs"] mod example_rails;
use std::{collections::{HashMap,BTreeMap},path::PathBuf,sync::Arc};
use compact_world::CompactWorld;
use compact_geometry::CompactGeometry;
fn root()->PathBuf {PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../compact-world-fixture")}
fn record(e:&compact_rails::Lip)->[u32;12] {
    let p=[e.a,e.b,e.normal,e.centroid];std::array::from_fn(|i|p[i/3].to_array()[i%3].to_bits())
}
fn keys(bake:&compact_rails::LipBake)->BTreeMap<example_rails::EdgeKey,[u32;12]> {
    bake.lips.iter().map(|e|(example_rails::edge_key(e.a,e.b),record(e))).collect()
}
#[test]
fn batched_owners_match_individual_queries_for_all_fixture_edges_and_boundaries() {
    let base=Arc::new(CompactWorld::load(&root().join("export/manifest.json")).unwrap());
    compact_rails::verify_owner_batching(&CompactGeometry::new(base.clone(),[0.;3]).unwrap());
    for name in ["overlay-add","overlay-noop","overlay-delete"] {
        let world=Arc::new(CompactWorld::with_overlay(&base,&root().join(name).join("manifest.json")).unwrap());
        compact_rails::verify_owner_batching(&CompactGeometry::new(world,[0.;3]).unwrap());
    }
}
#[test]
fn compact_global_lips_match_original_predicates_against_every_triangle() {
    let source=CompactGeometry::new(Arc::new(CompactWorld::load(&root().join("export/manifest.json")).unwrap()),[0.;3]).unwrap();
    let triangles:Vec<_>=source.world.groups().iter().flat_map(|g|g.triangle_range.clone()).filter_map(|id|{
        let t=source.raw(id);((t[1]-t[0]).cross(t[2]-t[0]).length_squared()>1e-16).then(||(id,compact_rails::map_triangle(&source,id)))
    }).collect();
    let mut edges=HashMap::new();for &(id,t) in &triangles {for (key,edge) in example_rails::face_candidates(id,t){edges.entry(key).or_insert(edge);}}
    let expected:BTreeMap<_,_>=edges.into_iter().filter(|(_,edge)|example_rails::candidate_is_lip(*edge,|o,d,l|triangles.iter().any(|(_,t)|example_rails::ray_triangle(o,d,l,t))))
        .map(|(k,e)|(k,record(&e.into()))).collect();
    let bake=compact_rails::bake(&source).unwrap();assert_eq!(keys(&bake),expected);assert!(!expected.is_empty());
    let file=root().join("fixture.lips");compact_rails::save(&file,&bake).unwrap();
    let loaded=compact_rails::load(&file,&source.world).unwrap();assert_eq!(keys(&loaded),expected);
    let mut legacy=std::fs::read(&file).unwrap();legacy[4..8].copy_from_slice(&2u32.to_le_bytes());
    let digest=blake3::hash(&legacy[120..]);legacy[88..120].copy_from_slice(digest.as_bytes());
    let legacy_file=root().join("fixture-legacy.lips");std::fs::write(&legacy_file,legacy).unwrap();
    assert_eq!(keys(&compact_rails::load(&legacy_file,&source.world).unwrap()),expected);
    assert!(compact_rails::load_with_identity(&legacy_file,&source.world,&bake.fingerprint).is_err(),"scene cache must require checksummed census");
    let mut bytes=std::fs::read(&file).unwrap();*bytes.last_mut().unwrap()^=1;std::fs::write(&file,&bytes).unwrap();
    assert!(compact_rails::load(&file,&source.world).is_err());
}
#[test]
fn terrain_seeded_probes_match_global_predicate_with_holes_and_objects() {
    let original=root().join("export");let folder=root().join("upward-terrain");std::fs::create_dir_all(folder.join("terrain")).unwrap();
    let mut manifest:serde_json::Value=serde_json::from_slice(&std::fs::read(original.join("manifest.json")).unwrap()).unwrap();
    for terrain in manifest["terrain"].as_array_mut().unwrap(){
        terrain["mirrored"]=false.into();
        for field in ["points","visibility"] {let path=terrain[field]["path"].as_str().unwrap();std::fs::copy(original.join(path),folder.join(path)).unwrap();}
    }
    for path in ["geometry.f64","instances.bin"] {std::fs::copy(original.join(path),folder.join(path)).unwrap();}
    std::fs::write(folder.join("manifest.json"),serde_json::to_vec(&manifest).unwrap()).unwrap();
    let source=CompactGeometry::new(Arc::new(CompactWorld::load(&folder.join("manifest.json")).unwrap()),[0.;3]).unwrap();
    let triangles:Vec<_>=source.world.groups().iter().flat_map(|g|g.triangle_range.clone()).filter_map(|id|{
        let t=source.raw(id);((t[1]-t[0]).cross(t[2]-t[0]).length_squared()>1e-16).then(||(id,compact_rails::map_triangle(&source,id)))
    }).collect();
    let mut edges=HashMap::new();for &(id,t) in &triangles {for (key,edge) in example_rails::face_candidates(id,t){edges.entry(key).or_insert(edge);}}
    assert!(edges.values().any(|e|!source.world.terrain_neighbors(e.owner).is_empty()),"fixture must exercise upward terrain candidates");
    let expected:BTreeMap<_,_>=edges.into_iter().filter(|(_,edge)|example_rails::candidate_is_lip(*edge,|o,d,l|triangles.iter().any(|(_,t)|example_rails::ray_triangle(o,d,l,t))))
        .map(|(k,e)|(k,record(&e.into()))).collect();
    assert_eq!(keys(&compact_rails::bake(&source).unwrap()),expected);
}
#[test]
fn overlay_grind_lips_match_fresh_global_bake_after_additions_and_deletions() {
    let base=Arc::new(CompactWorld::load(&root().join("export/manifest.json")).unwrap());
    let source=CompactGeometry::new(base.clone(),[0.;3]).unwrap();let bake=compact_rails::bake(&source).unwrap();
    for name in ["overlay-add","overlay-delete","overlay-disabled"] {
        let view=Arc::new(CompactWorld::with_overlay(&base,&root().join(name).join("manifest.json")).unwrap());
        let source=CompactGeometry::new(view,[0.;3]).unwrap();
        let changed=compact_rails::overlay(&bake,&source).unwrap();let expected=compact_rails::bake(&source).unwrap();
        assert_eq!(keys(&changed),keys(&expected),"{name}");
        assert_eq!(changed.candidates,expected.candidates,"candidate census {name}");
    }
}
#[test]
fn local_lip_update_matches_global_bake_when_one_live_instance_moves() {
    use sha2::{Digest,Sha256};
    let base=Arc::new(CompactWorld::load(&root().join("export/manifest.json")).unwrap());
    let before=Arc::new(CompactWorld::with_overlay(&base,&root().join("overlay-noop/manifest.json")).unwrap());
    let before=CompactGeometry::new(before,[0.;3]).unwrap();let previous=compact_rails::bake(&before).unwrap();
    let original=root().join("overlay-noop");let changed=root().join("overlay-move-one");std::fs::create_dir_all(&changed).unwrap();
    let mut manifest:serde_json::Value=serde_json::from_slice(&std::fs::read(original.join("manifest.json")).unwrap()).unwrap();
    let mut rows=std::fs::read(original.join("instances.bin")).unwrap();
    let scalar=|bytes:&[u8],offset:usize|f64::from_le_bytes(bytes[offset..offset+8].try_into().unwrap());
    let old=compact_world::Bounds{min:std::array::from_fn(|i|scalar(&rows,104+i*8)),max:std::array::from_fn(|i|scalar(&rows,128+i*8))};
    for offset in [80,104,128] {let value=scalar(&rows,offset)+4000.;rows[offset..offset+8].copy_from_slice(&value.to_le_bytes());}
    let new=compact_world::Bounds{min:std::array::from_fn(|i|scalar(&rows,104+i*8)),max:std::array::from_fn(|i|scalar(&rows,128+i*8))};
    let digest:String=Sha256::digest(&rows).iter().map(|b|format!("{b:02x}")).collect();
    manifest["instance_file"]["sha256"]=digest.clone().into();manifest["source_fingerprint"]=digest.into();
    std::fs::write(changed.join("instances.bin"),rows).unwrap();std::fs::copy(original.join("geometry.f64"),changed.join("geometry.f64")).unwrap();
    std::fs::write(changed.join("manifest.json"),serde_json::to_vec(&manifest).unwrap()).unwrap();
    let after=Arc::new(CompactWorld::with_overlay(&base,&changed.join("manifest.json")).unwrap());
    let after=CompactGeometry::new(after,[0.;3]).unwrap();
    let difference=after.world.difference_bounds(&before.world).unwrap();assert_eq!(difference.len(),2);
    for (actual,serialized) in difference.iter().zip([old,new]) {for axis in 0..3 {
        assert!(actual.min[axis]<=serialized.min[axis]&&actual.max[axis]>=serialized.max[axis]);
        assert!((actual.min[axis]-serialized.min[axis]).abs()<1e-8&&(actual.max[axis]-serialized.max[axis]).abs()<1e-8);
    }}
    let updated=compact_rails::update(&previous,&before,&after,difference).unwrap();let expected=compact_rails::bake(&after).unwrap();
    assert_eq!(keys(&updated),keys(&expected));assert_eq!(updated.candidates,expected.candidates);
}

#[test]
#[ignore="Read-only benchmark using explicitly supplied owned snapshots and verified earlier bake"]
fn recorded_delta_update(){
    let required=|name:&str|std::env::var(name).unwrap_or_else(|_|panic!("Missing {name}"));
    let base=Arc::new(CompactWorld::load(&PathBuf::from(required("SKATE_WORLD_MANIFEST"))).unwrap());
    let before=CompactGeometry::new(Arc::new(CompactWorld::with_overlay(&base,&PathBuf::from(required("SKATE_PREVIOUS_SCENE"))).unwrap()),[0.;3]).unwrap();
    let after=CompactGeometry::new(Arc::new(CompactWorld::with_overlay(&base,&PathBuf::from(required("SKATE_NEXT_SCENE"))).unwrap()),[0.;3]).unwrap();
    assert_eq!(before.world.scene_fingerprint.as_deref(),Some(required("SKATE_PREVIOUS_FINGERPRINT").as_str()));
    let previous=compact_rails::load_with_identity(&PathBuf::from(required("SKATE_PREVIOUS_LIPS")),&before.world,&required("SKATE_PREVIOUS_LIP_ID")).unwrap();
    let started=std::time::Instant::now();let difference=after.world.difference_bounds(&before.world).unwrap();let bound_count=difference.len();
    let updated=compact_rails::update(&previous,&before,&after,difference).unwrap();let update_ms=started.elapsed().as_secs_f64()*1000.;
    let started=std::time::Instant::now();let rails=compact_rails::rails(&updated);let merge_ms=started.elapsed().as_secs_f64()*1000.;
    println!("RECORDED_RAIL_DELTA bounds={bound_count} update_ms={update_ms:.3} merge_ms={merge_ms:.3} candidates={} lips={} rails={}",updated.candidates,updated.lips.len(),rails.len());
}
#[test]
#[ignore="One-time original rail bake over the owned whole-world geometry"]
fn bake_owned_complete_world_once() {
    let manifest=std::env::var_os("SKATE_WORLD_MANIFEST").map(PathBuf::from)
        .unwrap_or_else(||PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../dragonwilds-map-data/compact-world-v1/manifest.json"));
    let folder=manifest.parent().unwrap();
    let world=Arc::new(CompactWorld::load(&manifest).unwrap());
    let source=CompactGeometry::new(world,[0.;3]).unwrap();let bake=compact_rails::bake(&source).unwrap();
    compact_rails::save(&folder.join("world.lips"),&bake).unwrap();
    let rails=compact_rails::rails(&bake);assert!(!rails.is_empty());
    eprintln!("WORLD_RAIL_COMPLETE candidates={} lips={} rails={}",bake.candidates,bake.lips.len(),rails.len());
}
#[test]
#[ignore="Read-only owned geometry sample for cold-bake optimization"]
fn compare_terrain_seed_against_global_source_sample() {
    let folder=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../dragonwilds-map-data/compact-world-v1");
    let source=CompactGeometry::new(Arc::new(CompactWorld::load(&folder.join("manifest.json")).unwrap()),[0.;3]).unwrap();
    compact_rails::benchmark_terrain_seed(&source,100_000);
}
