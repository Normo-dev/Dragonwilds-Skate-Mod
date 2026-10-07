//! Preserve the exact ordered result of an incremental scene update. This is
//! deliberately separate from pristine-base -> scene caches: incremental lip
//! ordering is history dependent, and must never be written under their keys.
use crate::{compact_geometry::CompactGeometry,compact_rails::{self,LipBake},merged_rail_cache};
use serde::{Deserialize,Serialize};
use serde_json::{Value,json};
use std::{fs,io::Write,path::{Path,PathBuf},sync::atomic::{AtomicU64,Ordering},time::Instant};

const SCHEMA:u32=1;
const RETAIN:usize=3;
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {schema:u32,base_fingerprint:String,scene_fingerprint:String,algorithm:String,payload:String,
    identity:String,candidates:usize,lips:usize,merged_file:String}
pub struct Restored {pub bake:LipBake,pub rails:Vec<Vec<[f32;3]>>,pub checkpoint:Value}

fn hex(value:&str)->bool{value.len()==64&&value.bytes().all(|c|c.is_ascii_digit()||(b'a'..=b'f').contains(&c))}
fn folder(scene:&Path)->Result<PathBuf,String>{Ok(scene.parent().and_then(Path::parent)
    .ok_or("Scene manifest has no cache parent")?.join("accepted-scene-cache"))}
fn algorithm()->String{
    let mut hash=blake3::Hasher::new();hash.update(b"Dragonwilds accepted incremental scene v1\0");
    for bytes in [include_bytes!("compact_rails.rs").as_slice(),include_bytes!("compact_geometry.rs").as_slice(),
        include_bytes!("compact_world.rs").as_slice(),include_bytes!("accepted_scene_cache.rs").as_slice(),
        include_bytes!("merged_rail_cache.rs").as_slice(),include_bytes!("../Cargo.lock").as_slice(),
        include_bytes!("../../skate3-mashup/crates/render_anim/src/skate/rails.rs").as_slice()] {
        hash.update(&(bytes.len() as u64).to_le_bytes());hash.update(bytes);
    }hash.finalize().to_hex().to_string()
}
fn identity(base:&str,scene:&str,algorithm:&str,payload:&str)->String{
    let mut hash=blake3::Hasher::new();hash.update(b"Dragonwilds exact accepted scene checkpoint v1\0");
    for value in [base,scene,algorithm,payload]{hash.update(&(value.len() as u64).to_le_bytes());hash.update(value.as_bytes());}
    hash.finalize().to_hex().to_string()
}
fn checkpoint(path:&Path,record:&Record)->Value{json!({"schema":SCHEMA,"durable":true,
    "format":"accepted_incremental_v1","scene_fingerprint":record.scene_fingerprint,"checkpoint_file":path})}
fn scene_identity(source:&CompactGeometry)->Result<&str,String>{
    if source.anchor!=[0.;3]{return Err("Accepted scene cache requires fixed origin".into());}
    let scene=source.world.scene_fingerprint.as_deref().ok_or("Accepted scene cache requires an overlay")?;
    if !hex(scene)||!hex(&source.world.source_fingerprint){return Err("Invalid accepted scene identity".into());}Ok(scene)
}
fn atomic_json(path:&Path,record:&Record)->Result<(),String>{
    static SERIAL:AtomicU64=AtomicU64::new(0);
    let temporary=path.with_extension(format!("tmp-{}-{}",std::process::id(),SERIAL.fetch_add(1,Ordering::Relaxed)));
    let result=(||{let mut file=fs::OpenOptions::new().write(true).create_new(true).open(&temporary).map_err(|e|e.to_string())?;
        let bytes=serde_json::to_vec(record).map_err(|e|e.to_string())?;file.write_all(&bytes).map_err(|e|e.to_string())?;
        file.sync_all().map_err(|e|e.to_string())?;drop(file);fs::rename(&temporary,path).map_err(|e|e.to_string())})();
    if result.is_err(){let _=fs::remove_file(temporary);}result
}
/// Called only by the collision builder thread. Disk failure leaves the exact
/// freshly derived rails usable, but never advertises a durable checkpoint.
pub fn prepare(scene:&Path,source:&CompactGeometry,bake:&LipBake)->Result<(Vec<Vec<[f32;3]>>,Option<Value>),String>{
    let started=Instant::now();let scene_id=scene_identity(source)?;
    if bake.fingerprint!=scene_id{return Err("Accepted lip checkpoint snapshot mismatch".into());}
    let root=folder(scene)?;let rail_manifest=root.join("manifest.json");
    let merged=merged_rail_cache::prepare(&rail_manifest,bake)?;
    let persist=(||{
        let merged_path=merged.path.as_ref().ok_or("Merged rails were not persisted")?;
        let payload=compact_rails::payload_digest(bake).to_hex().to_string();let algorithm=algorithm();
        let id=identity(&source.world.source_fingerprint,scene_id,&algorithm,&payload);
        fs::create_dir_all(&root).map_err(|e|e.to_string())?;
        let lip_file=root.join(format!("{id}.lips"));
        // A checksummed existing immutable file needs no repeat write.
        if compact_rails::load_with_identity(&lip_file,&source.world,&id).is_err(){compact_rails::save_with_identity(&lip_file,bake,&id)?;}
        let merged_file=merged_path.file_name().and_then(|s|s.to_str()).ok_or("Invalid merged cache filename")?.to_string();
        let record=Record{schema:SCHEMA,base_fingerprint:source.world.source_fingerprint.clone(),scene_fingerprint:scene_id.into(),
            algorithm,payload,identity:id,candidates:bake.candidates,lips:bake.lips.len(),merged_file};
        let marker=root.join(format!("{scene_id}.json"));atomic_json(&marker,&record)?;
        prune(&root,&marker);
        Ok::<_,String>(checkpoint(&marker,&record))
    })();
    let status=match persist{Ok(value)=>{eprintln!("WORLD_ACCEPTED_SCENE_CACHE saved elapsed_ms={}",started.elapsed().as_millis());Some(value)},
        Err(error)=>{eprintln!("WORLD_ACCEPTED_SCENE_CACHE write failed (computed scene retained): {error}");None}};
    Ok((merged.rails,status))
}
pub fn load(scene:&Path,source:&CompactGeometry)->Result<Restored,String>{
    let started=Instant::now();let scene_id=scene_identity(source)?;let root=folder(scene)?;
    let marker=root.join(format!("{scene_id}.json"));
    if fs::metadata(&marker).map_err(|e|e.to_string())?.len()>8192{return Err("Oversized accepted scene marker".into());}
    let record:Record=serde_json::from_slice(&fs::read(&marker).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    if record.schema!=SCHEMA||record.base_fingerprint!=source.world.source_fingerprint||record.scene_fingerprint!=scene_id
        ||record.algorithm!=algorithm()||!hex(&record.payload)||!hex(&record.identity)
        ||record.identity!=identity(&record.base_fingerprint,scene_id,&record.algorithm,&record.payload){return Err("Accepted scene checkpoint identity mismatch".into());}
    let bake=compact_rails::load_with_identity(&root.join(format!("{}.lips",record.identity)),&source.world,&record.identity)?;
    if bake.candidates!=record.candidates||bake.lips.len()!=record.lips||compact_rails::payload_digest(&bake).to_hex().as_str()!=record.payload{
        return Err("Accepted scene checkpoint payload mismatch".into());}
    let expected=format!("{}.rails",blake3::Hash::from(merged_rail_cache::key(&bake)).to_hex());
    if record.merged_file!=expected{return Err("Accepted scene merged identity mismatch".into());}
    // The original merged-cache implementation validates all headers, ordering,
    // float bits and checksum. If only that cache is damaged it recreates the
    // original merge from verified lips, never re-probing the world geometry.
    let merged=merged_rail_cache::prepare(&root.join("manifest.json"),&bake)?;
    if merged.path.is_none(){return Err("Accepted scene merged rails unavailable".into());}
    eprintln!("WORLD_ACCEPTED_SCENE_CACHE hit lips={} rails={} elapsed_ms={}",bake.lips.len(),merged.rails.len(),started.elapsed().as_millis());
    Ok(Restored{bake,rails:merged.rails,checkpoint:checkpoint(&marker,&record)})
}
fn prune(root:&Path,current:&Path){
    // Bound the rolling checkpoint cache. Only our exact digest filenames are
    // eligible; no recursive deletion and no paths supplied by marker contents.
    let Ok(entries)=fs::read_dir(root)else{return;};let mut markers=Vec::new();
    for entry in entries.flatten(){let path=entry.path();if path.extension().is_some_and(|s|s=="json")
        &&path.file_stem().and_then(|s|s.to_str()).is_some_and(hex){
        if let Ok(meta)=entry.metadata(){if meta.is_file(){markers.push((path,meta.modified().ok()));}}
    }}
    markers.sort_by(|a,b|b.1.cmp(&a.1));
    let mut keep=std::collections::HashSet::new();keep.insert(current.to_path_buf());
    for (path,_) in &markers{if keep.len()<RETAIN{keep.insert(path.clone());}}
    let mut lips=std::collections::HashSet::new();let mut rails=std::collections::HashSet::new();
    for (path,_) in markers{
        if !keep.contains(&path){let _=fs::remove_file(path);continue;}
        // A corrupt retained marker must not trigger destructive cache cleanup.
        let Ok(bytes)=fs::read(&path)else{return;};let Ok(record)=serde_json::from_slice::<Record>(&bytes)else{return;};
        if !hex(&record.identity)||!record.merged_file.strip_suffix(".rails").is_some_and(hex){return;}
        lips.insert(format!("{}.lips",record.identity));rails.insert(record.merged_file);
    }
    for (folder,extension,referenced) in [(root.to_path_buf(),"lips",lips),(root.join("merged-rails-cache"),"rails",rails)]{
        let Ok(entries)=fs::read_dir(folder)else{continue;};for entry in entries.flatten(){let path=entry.path();
            if path.extension().is_some_and(|s|s==extension)&&path.file_stem().and_then(|s|s.to_str()).is_some_and(hex)
                &&path.file_name().and_then(|s|s.to_str()).is_some_and(|s|!referenced.contains(s))
                &&entry.file_type().is_ok_and(|t|t.is_file()){let _=fs::remove_file(path);}
        }
    }
}
