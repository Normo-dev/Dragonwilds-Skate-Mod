#[path="../src/compact_world.rs"] mod compact_world;
#[path="../src/compact_geometry.rs"] mod compact_geometry;
#[path="../src/compact_rails.rs"] mod compact_rails;
#[path="../src/merged_rail_cache.rs"] mod merged_rail_cache;
#[path="../src/accepted_scene_cache.rs"] mod accepted_scene_cache;
#[path="../../skate3-mashup/crates/render_anim/src/skate/rails.rs"] mod example_rails;
use std::{fs,path::PathBuf,sync::{Arc,atomic::{AtomicU64,Ordering}}};
use compact_world::CompactWorld;
use compact_geometry::CompactGeometry;
fn source(name:&str)->CompactGeometry{
    let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../compact-world-fixture");
    let base=Arc::new(CompactWorld::load(&root.join("export/manifest.json")).unwrap());
    CompactGeometry::new(Arc::new(CompactWorld::with_overlay(&base,&root.join(name).join("manifest.json")).unwrap()),[0.;3]).unwrap()
}
fn temporary()->PathBuf{static N:AtomicU64=AtomicU64::new(0);let p=std::env::temp_dir().join(format!("s3-accepted-scene-test-{}-{}",std::process::id(),N.fetch_add(1,Ordering::Relaxed)));fs::create_dir(&p).unwrap();p}
fn cleanup(p:PathBuf){let p=p.canonicalize().unwrap();assert!(p.starts_with(std::env::temp_dir().canonicalize().unwrap()));assert!(p.file_name().unwrap().to_str().unwrap().starts_with("s3-accepted-scene-test-"));fs::remove_dir_all(p).unwrap();}
fn bits(bake:&compact_rails::LipBake)->Vec<[u32;12]>{bake.lips.iter().map(|e|{let points=[e.a,e.b,e.normal,e.centroid];std::array::from_fn(|i|points[i/3].to_array()[i%3].to_bits())}).collect()}
fn rail_bits(rails:&[Vec<[f32;3]>])->Vec<Vec<[u32;3]>>{rails.iter().map(|r|r.iter().map(|p|p.map(f32::to_bits)).collect()).collect()}
#[test]
fn incremental_checkpoint_keeps_exact_history_and_original_merged_result(){
    let dir=temporary();let scene=dir.join("overlay/manifest.json");let source=source("overlay-add");
    let mut bake=compact_rails::bake(&source).unwrap();bake.lips.reverse(); // valid alternate incremental history
    let (rails,status)=accepted_scene_cache::prepare(&scene,&source,&bake).unwrap();assert!(status.unwrap()["durable"].as_bool().unwrap());
    let warm=accepted_scene_cache::load(&scene,&source).unwrap();assert_eq!(bits(&bake),bits(&warm.bake));
    assert_eq!(rail_bits(&rails),rail_bits(&warm.rails));assert_eq!(bake.candidates,warm.bake.candidates);
    assert!(!dir.join("scene-lips-cache").exists(),"incremental history must not impersonate pristine overlay cache");cleanup(dir);
}
#[test]
fn wrong_scene_changed_marker_and_corrupt_lips_are_rejected(){
    let dir=temporary();let scene=dir.join("overlay/manifest.json");let view=source("overlay-add");let bake=compact_rails::bake(&view).unwrap();
    let(_,status)=accepted_scene_cache::prepare(&scene,&view,&bake).unwrap();let marker=PathBuf::from(status.unwrap()["checkpoint_file"].as_str().unwrap());
    let original=fs::read(&marker).unwrap();let record:serde_json::Value=serde_json::from_slice(&original).unwrap();
    assert!(accepted_scene_cache::load(&scene,&source("overlay-delete")).is_err());
    for field in ["base_fingerprint","scene_fingerprint","algorithm","payload","identity","merged_file"]{
        let mut changed=record.clone();changed[field]="../wrong".into();fs::write(&marker,serde_json::to_vec(&changed).unwrap()).unwrap();
        assert!(accepted_scene_cache::load(&scene,&view).is_err(),"{field}");
    }fs::write(&marker,&original).unwrap();
    let lips=marker.parent().unwrap().join(format!("{}.lips",record["identity"].as_str().unwrap()));let mut bad=fs::read(&lips).unwrap();let last=bad.len()-1;bad[last]^=1;fs::write(&lips,&bad).unwrap();
    assert!(accepted_scene_cache::load(&scene,&view).is_err());cleanup(dir);
}
#[test]
fn rolling_cache_retains_three_complete_checkpoints_and_ignores_unowned_files(){
    let dir=temporary();let scene=dir.join("overlay/manifest.json");let cache=dir.join("accepted-scene-cache");fs::create_dir(&cache).unwrap();
    fs::write(cache.join("keep.lips"),b"user data").unwrap();fs::write(cache.join("keep.json"),b"user data").unwrap();
    let mut latest=None;
    for name in ["overlay-add","overlay-delete","overlay-noop","overlay-two","overlay-two-moved"]{
        let source=source(name);let bake=compact_rails::bake(&source).unwrap();accepted_scene_cache::prepare(&scene,&source,&bake).unwrap();
        assert_eq!(bits(&accepted_scene_cache::load(&scene,&source).unwrap().bake),bits(&bake));latest=Some(source);
    }
    assert!(accepted_scene_cache::load(&scene,&latest.unwrap()).is_ok());
    let count=|path:&std::path::Path,ext:&str|fs::read_dir(path).unwrap().flatten().filter(|e|e.path().extension().is_some_and(|s|s==ext)&&e.path().file_stem().unwrap().len()==64).count();
    assert_eq!(count(&cache,"json"),3);assert_eq!(count(&cache,"lips"),3);assert_eq!(count(&cache.join("merged-rails-cache"),"rails"),3);
    assert_eq!(fs::read(cache.join("keep.lips")).unwrap(),b"user data");assert_eq!(fs::read(cache.join("keep.json")).unwrap(),b"user data");cleanup(dir);
}
#[test]
fn failed_checkpoint_write_does_not_discard_valid_computed_rails(){
    let dir=temporary();let scene=dir.join("overlay/manifest.json");let view=source("overlay-add");let bake=compact_rails::bake(&view).unwrap();
    fs::write(dir.join("accepted-scene-cache"),b"not a directory").unwrap();let(rails,status)=accepted_scene_cache::prepare(&scene,&view,&bake).unwrap();
    assert_eq!(rail_bits(&rails),rail_bits(&compact_rails::rails(&bake)));assert!(status.is_none());assert!(accepted_scene_cache::load(&scene,&view).is_err());cleanup(dir);
}
