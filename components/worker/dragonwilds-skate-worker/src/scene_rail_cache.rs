//! Cache only pristine-base -> accepted-scene lip preparation. Incremental
//! histories keep their own original input ordering and never populate this key.
use crate::{compact_geometry::CompactGeometry,compact_rails::{self,LipBake}};
use std::{path::{Path,PathBuf},time::Instant};

pub fn key(base:&LipBake,source:&CompactGeometry)->Result<String,String> {
    let scene=source.world.scene_fingerprint.as_deref().ok_or("Scene lip cache requires an overlay")?;
    if base.fingerprint!=source.world.source_fingerprint||source.anchor!=[0.;3] {return Err("Scene lip cache base/origin mismatch".into());}
    let mut hash=blake3::Hasher::new();hash.update(b"Dragonwilds scene lip cache v1\0");
    for value in [&base.fingerprint,scene] {hash.update(&(value.len() as u64).to_le_bytes());hash.update(value.as_bytes());}
    hash.update(compact_rails::payload_digest(base).as_bytes());
    // Algorithm, quantization, transforms, probe halo and original predicates
    // all participate. Recompiling unrelated worker transport leaves this key.
    for bytes in [include_bytes!("compact_rails.rs").as_slice(),include_bytes!("compact_geometry.rs").as_slice(),
        include_bytes!("compact_world.rs").as_slice(),include_bytes!("scene_rail_cache.rs").as_slice(),
        include_bytes!("../Cargo.lock").as_slice(),
        include_bytes!("../../skate3-mashup/crates/render_anim/src/skate/rails.rs").as_slice()] {
        hash.update(&(bytes.len() as u64).to_le_bytes());hash.update(bytes);
    }
    Ok(hash.finalize().to_hex().to_string())
}
pub fn path(scene_manifest:&Path,key:&str)->Result<PathBuf,String> {
    let root=scene_manifest.parent().and_then(Path::parent).ok_or("Scene manifest has no cache parent")?;
    Ok(root.join("scene-lips-cache").join(format!("{key}.lips")))
}
pub fn prepare(base:&LipBake,source:&CompactGeometry,scene_manifest:&Path)->Result<LipBake,String> {
    prepare_with(base,source,scene_manifest,||compact_rails::overlay(base,source))
}
fn prepare_with(base:&LipBake,source:&CompactGeometry,scene_manifest:&Path,
    build:impl FnOnce()->Result<LipBake,String>)->Result<LipBake,String> {
    let started=Instant::now();let key=key(base,source)?;let path=path(scene_manifest,&key)?;
    match compact_rails::load_with_identity(&path,&source.world,&key) {
        Ok(bake)=>{eprintln!("WORLD_SCENE_LIP_CACHE hit key={key} lips={} elapsed_ms={}",bake.lips.len(),started.elapsed().as_millis());return Ok(bake)},
        Err(error)=>eprintln!("WORLD_SCENE_LIP_CACHE preparing key={key}: {error}"),
    }
    let bake=build()?;
    if bake.fingerprint!=source.world.scene_fingerprint.as_deref().unwrap_or("") {return Err("Scene lip preparation returned the wrong snapshot".into());}
    let write=std::fs::create_dir_all(path.parent().unwrap()).map_err(|e|e.to_string())
        .and_then(|()|compact_rails::save_with_identity(&path,&bake,&key));
    match write {
        Ok(())=>eprintln!("WORLD_SCENE_LIP_CACHE saved key={key} lips={} elapsed_ms={}",bake.lips.len(),started.elapsed().as_millis()),
        Err(error)=>eprintln!("WORLD_SCENE_LIP_CACHE write failed (computed world remains valid): {error}"),
    }
    Ok(bake)
}

#[cfg(test)]
pub fn prepare_test(base:&LipBake,source:&CompactGeometry,scene_manifest:&Path,
    build:impl FnOnce()->Result<LipBake,String>)->Result<LipBake,String> {
    prepare_with(base,source,scene_manifest,build)
}
