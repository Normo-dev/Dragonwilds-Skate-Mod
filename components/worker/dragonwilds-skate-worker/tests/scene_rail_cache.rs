#[path="../src/compact_world.rs"] mod compact_world;
#[path="../src/compact_geometry.rs"] mod compact_geometry;
#[path="../src/compact_rails.rs"] mod compact_rails;
#[path="../src/scene_rail_cache.rs"] mod scene_rail_cache;
#[path="../../skate3-mashup/crates/render_anim/src/skate/rails.rs"] mod example_rails;
use std::{path::PathBuf,sync::Arc,cell::Cell};
use compact_world::CompactWorld;
use compact_geometry::CompactGeometry;
fn root()->PathBuf {PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../compact-world-fixture")}
#[test]
fn scene_cache_preserves_exact_lips_and_rebuilds_only_invalid_or_changed_content() {
    let base=Arc::new(CompactWorld::load(&root().join("export/manifest.json")).unwrap());
    let base_source=CompactGeometry::new(base.clone(),[0.;3]).unwrap();let bake=compact_rails::bake(&base_source).unwrap();
    let scene=root().join("overlay-add/manifest.json");
    let source=CompactGeometry::new(Arc::new(CompactWorld::with_overlay(&base,&scene).unwrap()),[0.;3]).unwrap();
    let private=root().join(format!("cache-test-{}",std::process::id()));
    std::fs::create_dir_all(private.join("scene")).unwrap();let location=private.join("scene/manifest.json");
    let key=scene_rail_cache::key(&bake,&source).unwrap();let cache=scene_rail_cache::path(&location,&key).unwrap();
    if cache.exists(){std::fs::remove_file(&cache).unwrap();}
    let builds=Cell::new(0);let prepare=||scene_rail_cache::prepare_test(&bake,&source,&location,||{
        builds.set(builds.get()+1);compact_rails::overlay(&bake,&source)
    }).unwrap();
    let cold=prepare();assert_eq!(builds.get(),1);let expected=compact_rails::payload_digest(&cold);
    let warm=prepare();assert_eq!(builds.get(),1);assert_eq!(expected,compact_rails::payload_digest(&warm));
    assert_eq!(cold.fingerprint,warm.fingerprint);
    let bytes=std::fs::read(&cache).unwrap();
    // Corrupt bytes, wrong embedded identity, short writes, invalid census and
    // checksum-valid nonfinite payloads all rebuild instead of becoming ready.
    let mut corrupt=bytes.clone();*corrupt.last_mut().unwrap()^=1;std::fs::write(&cache,corrupt).unwrap();prepare();assert_eq!(builds.get(),2);
    let mut wrong=bytes.clone();wrong[24]^=1;std::fs::write(&cache,wrong).unwrap();prepare();assert_eq!(builds.get(),3);
    std::fs::write(&cache,&bytes[..80]).unwrap();prepare();assert_eq!(builds.get(),4);
    let mut huge=bytes.clone();huge[8..16].copy_from_slice(&u64::MAX.to_le_bytes());std::fs::write(&cache,huge).unwrap();prepare();assert_eq!(builds.get(),5);
    let mut census=bytes.clone();census[16..24].copy_from_slice(&(cold.candidates as u64+1).to_le_bytes());std::fs::write(&cache,census).unwrap();prepare();assert_eq!(builds.get(),6);
    let mut nonfinite=compact_rails::load_with_identity(&cache,&source.world,&key).unwrap();nonfinite.lips[0].a.x=f32::NAN;
    compact_rails::save_with_identity(&cache,&nonfinite,&key).unwrap();prepare();assert_eq!(builds.get(),7);
    // An interrupted temporary output never becomes a hit; a failed builder
    // never publishes a complete cache record.
    std::fs::remove_file(&cache).unwrap();std::fs::write(cache.with_extension("interrupted.tmp"),&bytes[..80]).unwrap();
    assert!(scene_rail_cache::prepare_test(&bake,&source,&location,||Err("interrupted preparation".into())).is_err());assert!(!cache.exists());
    prepare();assert_eq!(builds.get(),8);
    let mut revised:serde_json::Value=serde_json::from_slice(&std::fs::read(&scene).unwrap()).unwrap();
    revised["scene_revision"]=999.into();revised["generation"]="later-capture".into();
    for name in ["geometry.f64","instances.bin"] {std::fs::copy(scene.parent().unwrap().join(name),private.join("scene").join(name)).unwrap();}
    std::fs::write(&location,serde_json::to_vec(&revised).unwrap()).unwrap();
    let same=CompactGeometry::new(Arc::new(CompactWorld::with_overlay(&base,&location).unwrap()),[0.;3]).unwrap();
    assert_eq!(scene_rail_cache::key(&bake,&same).unwrap(),key);
    let reloaded=scene_rail_cache::prepare_test(&bake,&same,&location,||panic!("identical content at a new revision must hit")).unwrap();
    assert_eq!(compact_rails::payload_digest(&reloaded),compact_rails::payload_digest(&prepare()));
    let changed=CompactGeometry::new(Arc::new(CompactWorld::with_overlay(&base,&root().join("overlay-delete/manifest.json")).unwrap()),[0.;3]).unwrap();
    assert_ne!(scene_rail_cache::key(&bake,&changed).unwrap(),key);
    let changed_builds=Cell::new(0);scene_rail_cache::prepare_test(&bake,&changed,&location,||{changed_builds.set(1);compact_rails::overlay(&bake,&changed)}).unwrap();assert_eq!(changed_builds.get(),1);
    // Upstream candidate enumeration can vary between bakes. Its actual saved
    // lip order participates, so a different base bake cannot reuse this file.
    let mut reordered=compact_rails::LipBake{fingerprint:bake.fingerprint.clone(),candidates:bake.candidates,lips:bake.lips.clone()};reordered.lips.reverse();
    assert_ne!(scene_rail_cache::key(&reordered,&source).unwrap(),key);
}
