//! Persist the exact ordered output of the original lip merge/chaining routine.
//! No geometry admission, merge predicate, coordinate arithmetic or query order
//! is replaced here. The first successful original result is reused verbatim.
use crate::compact_rails::{self,LipBake};
use std::{io::{BufReader,BufWriter,Read,Write},path::{Path,PathBuf},sync::atomic::{AtomicU64,Ordering},time::Instant};

const VERSION:u32=1;
const HEADER:usize=88;
pub struct Prepared {pub rails:Vec<Vec<[f32;3]>>,pub path:Option<PathBuf>}
pub fn key(bake:&LipBake)->[u8;32] {
    let mut hash=blake3::Hasher::new();hash.update(b"Dragonwilds exact ordered merged rails v1\0");
    hash.update(&(bake.fingerprint.len() as u64).to_le_bytes());hash.update(bake.fingerprint.as_bytes());
    hash.update(compact_rails::payload_digest(bake).as_bytes());
    for bytes in [include_bytes!("compact_rails.rs").as_slice(),include_bytes!("merged_rail_cache.rs").as_slice(),
        include_bytes!("../Cargo.lock").as_slice(),
        include_bytes!("../../skate3-mashup/crates/render_anim/src/skate/rails.rs").as_slice()] {
        hash.update(&(bytes.len() as u64).to_le_bytes());hash.update(bytes);
    }
    *hash.finalize().as_bytes()
}
fn cache_path(manifest:&Path,identity:&[u8;32])->Result<PathBuf,String>{
    let root=manifest.parent().ok_or("Merged rail manifest has no parent")?;
    Ok(root.join("merged-rails-cache").join(format!("{}.rails",blake3::Hash::from(*identity).to_hex())))
}
fn limits(bake:&LipBake)->Result<(usize,usize),String>{
    Ok((bake.lips.len(),bake.lips.len().checked_mul(2).ok_or("Merged rail limit overflow")?))
}
fn load(path:&Path,identity:&[u8;32],bake:&LipBake)->Result<Vec<Vec<[f32;3]>>,String>{
    let file=std::fs::File::open(path).map_err(|e|e.to_string())?;
    let size=file.metadata().map_err(|e|e.to_string())?.len();
    let mut file=BufReader::with_capacity(1024*1024,file);let mut header=[0u8;HEADER];
    file.read_exact(&mut header).map_err(|e|e.to_string())?;
    let u64_at=|i|u64::from_le_bytes(header[i..i+8].try_into().unwrap());
    if &header[..4]!=b"S3R1"||u32::from_le_bytes(header[4..8].try_into().unwrap())!=VERSION||&header[24..56]!=identity {
        return Err("Merged rail identity/version mismatch".into());
    }
    let count=usize::try_from(u64_at(8)).map_err(|_|"Merged rail count overflow")?;
    let points=usize::try_from(u64_at(16)).map_err(|_|"Merged point count overflow")?;
    let(max_rails,max_points)=limits(bake)?;
    let expected=(HEADER as u64).checked_add((count as u64).checked_mul(8).ok_or("Merged size overflow")?)
        .and_then(|v|v.checked_add((points as u64).checked_mul(12)?)).ok_or("Merged size overflow")?;
    if count>max_rails||points>max_points||points<count.checked_mul(2).ok_or("Merged minimum overflow")?||size!=expected {
        return Err("Invalid merged rail count/size".into());
    }
    let mut hash=blake3::Hasher::new();hash.update(&header[..56]);
    let mut rails=Vec::with_capacity(count);let mut consumed=0usize;
    for _ in 0..count {
        let mut bytes=[0u8;8];file.read_exact(&mut bytes).map_err(|e|e.to_string())?;hash.update(&bytes);
        let len=usize::try_from(u64::from_le_bytes(bytes)).map_err(|_|"Merged polyline overflow")?;
        if len<2||len>points-consumed{return Err("Invalid merged polyline boundary".into());}
        let mut rail=Vec::with_capacity(len);
        for _ in 0..len {
            let mut bytes=[0u8;12];file.read_exact(&mut bytes).map_err(|e|e.to_string())?;hash.update(&bytes);
            let point=std::array::from_fn(|i|f32::from_le_bytes(bytes[i*4..i*4+4].try_into().unwrap()));
            if !point.iter().all(|x|x.is_finite()){return Err("Non-finite merged point".into());}
            rail.push(point);
        }
        consumed+=len;rails.push(rail);
    }
    if consumed!=points||hash.finalize().as_bytes()!=&header[56..88]{return Err("Merged rail checksum/count mismatch".into());}
    Ok(rails)
}
fn save(path:&Path,identity:&[u8;32],bake:&LipBake,rails:&[Vec<[f32;3]>])->Result<(),String>{
    let(max_rails,max_points)=limits(bake)?;
    let points=rails.iter().try_fold(0usize,|n,r|n.checked_add(r.len())).ok_or("Merged point overflow")?;
    if rails.len()>max_rails||points>max_points||rails.iter().any(|r|r.len()<2||r.iter().flatten().any(|v|!v.is_finite())) {
        return Err("Invalid original merged rail result".into());
    }
    let mut header=[0u8;HEADER];header[..4].copy_from_slice(b"S3R1");header[4..8].copy_from_slice(&VERSION.to_le_bytes());
    header[8..16].copy_from_slice(&(rails.len() as u64).to_le_bytes());header[16..24].copy_from_slice(&(points as u64).to_le_bytes());header[24..56].copy_from_slice(identity);
    let mut hash=blake3::Hasher::new();hash.update(&header[..56]);
    for rail in rails {hash.update(&(rail.len() as u64).to_le_bytes());for point in rail {for v in point {hash.update(&v.to_le_bytes());}}}
    header[56..88].copy_from_slice(hash.finalize().as_bytes());
    std::fs::create_dir_all(path.parent().unwrap()).map_err(|e|e.to_string())?;
    static SERIAL:AtomicU64=AtomicU64::new(0);
    let temporary=path.with_extension(format!("tmp-{}-{}",std::process::id(),SERIAL.fetch_add(1,Ordering::Relaxed)));
    let result=(||{
        let file=std::fs::OpenOptions::new().write(true).create_new(true).open(&temporary).map_err(|e|e.to_string())?;
        let mut out=BufWriter::with_capacity(1024*1024,file);out.write_all(&header).map_err(|e|e.to_string())?;
        for rail in rails {out.write_all(&(rail.len() as u64).to_le_bytes()).map_err(|e|e.to_string())?;
            for point in rail {for v in point {out.write_all(&v.to_le_bytes()).map_err(|e|e.to_string())?;}}}
        out.flush().map_err(|e|e.to_string())?;out.get_ref().sync_all().map_err(|e|e.to_string())?;drop(out);
        std::fs::rename(&temporary,path).map_err(|e|e.to_string())
    })();
    if result.is_err(){let _=std::fs::remove_file(temporary);}result
}
pub fn prepare(manifest:&Path,bake:&LipBake)->Result<Prepared,String>{
    prepare_with(manifest,bake,||Ok(compact_rails::rails(bake)))
}
fn prepare_with(manifest:&Path,bake:&LipBake,build:impl FnOnce()->Result<Vec<Vec<[f32;3]>>,String>)->Result<Prepared,String>{
    let started=Instant::now();let identity=key(bake);let path=cache_path(manifest,&identity)?;
    match load(&path,&identity,bake){
        Ok(rails)=>{eprintln!("WORLD_MERGED_RAIL_CACHE hit rails={} elapsed_ms={}",rails.len(),started.elapsed().as_millis());return Ok(Prepared{rails,path:Some(path)})},
        Err(e)=>eprintln!("WORLD_MERGED_RAIL_CACHE preparing: {e}"),
    }
    let rails=build()?;
    let persisted=match save(&path,&identity,bake,&rails){
        Ok(())=>{eprintln!("WORLD_MERGED_RAIL_CACHE saved rails={} elapsed_ms={}",rails.len(),started.elapsed().as_millis());Some(path)},
        Err(e)=>{eprintln!("WORLD_MERGED_RAIL_CACHE write failed (original result retained): {e}");None},
    };
    Ok(Prepared{rails,path:persisted})
}

#[cfg(test)]
mod tests {
    use super::*;
    use bevy::math::Vec3;
    fn fixture()->LipBake{LipBake{fingerprint:"fixture".into(),candidates:8,lips:(0..4).map(|i|compact_rails::Lip{
        a:Vec3::new(i as f32,0.,0.),b:Vec3::new(i as f32+1.,0.,0.),normal:Vec3::Z,centroid:Vec3::ZERO}).collect()}}
    fn directory()->PathBuf{static N:AtomicU64=AtomicU64::new(0);let p=std::env::temp_dir().join(format!("s3-merged-rail-test-{}-{}",std::process::id(),N.fetch_add(1,Ordering::Relaxed)));std::fs::create_dir(&p).unwrap();p}
    fn cleanup(dir:PathBuf){let absolute=dir.canonicalize().unwrap();assert!(absolute.starts_with(std::env::temp_dir().canonicalize().unwrap()));assert!(absolute.file_name().unwrap().to_str().unwrap().starts_with("s3-merged-rail-test-"));std::fs::remove_dir_all(absolute).unwrap();}
    fn sample()->Vec<Vec<[f32;3]>>{vec![vec![[0.,-0.,1.],[3.,2.,1.],[0.,-0.,1.]],vec![[9.,8.,7.],[6.,5.,4.]]]}
    fn bits(v:&[Vec<[f32;3]>])->Vec<Vec<[u32;3]>>{v.iter().map(|r|r.iter().map(|p|p.map(f32::to_bits)).collect()).collect()}
    #[test]fn cold_warm_reuses_exact_order_boundaries_and_float_bits(){let dir=directory();let bake=fixture();let file=dir.join("manifest.json");let expected=sample();
        let a=prepare_with(&file,&bake,||Ok(expected.clone())).unwrap();let b=prepare_with(&file,&bake,||panic!("valid cache rebuilt")).unwrap();assert_eq!(bits(&a.rails),bits(&b.rails));assert_eq!(bits(&b.rails),bits(&expected));cleanup(dir);}
    #[test]fn damaged_headers_payload_boundaries_and_nan_rebuild(){let dir=directory();let bake=fixture();let identity=key(&bake);let file=dir.join("manifest.json");let path=cache_path(&file,&identity).unwrap();let expected=sample();save(&path,&identity,&bake,&expected).unwrap();let valid=std::fs::read(&path).unwrap();
        for kind in 0..7 {let mut bad=valid.clone();match kind{0=>bad[8..16].copy_from_slice(&u64::MAX.to_le_bytes()),1=>bad[16]^=1,2=>bad[24]^=1,3=>{bad.pop();},4=>bad[HEADER..HEADER+8].copy_from_slice(&1u64.to_le_bytes()),5=>bad[HEADER+8..HEADER+12].copy_from_slice(&f32::NAN.to_le_bytes()),_=>bad[HEADER+12]^=1}
            if kind==4||kind==5{let mut hash=blake3::Hasher::new();hash.update(&bad[..56]);hash.update(&bad[HEADER..]);bad[56..88].copy_from_slice(hash.finalize().as_bytes());}
            std::fs::write(&path,bad).unwrap();assert!(load(&path,&identity,&bake).is_err());let restored=prepare_with(&file,&bake,||Ok(expected.clone())).unwrap();assert_eq!(bits(&restored.rails),bits(&expected));assert!(load(&path,&identity,&bake).is_ok());}
        cleanup(dir);}
    #[test]fn input_identity_and_order_change_keys_and_failed_build_never_publishes(){let dir=directory();let bake=fixture();let mut other=fixture();other.lips.reverse();assert_ne!(key(&bake),key(&other));other=fixture();other.candidates+=1;assert_ne!(key(&bake),key(&other));other=fixture();other.fingerprint.push('x');assert_ne!(key(&bake),key(&other));
        let file=dir.join("manifest.json");let path=cache_path(&file,&key(&bake)).unwrap();assert!(prepare_with(&file,&bake,||Err("expected".into())).is_err());assert!(!path.exists());std::fs::create_dir_all(path.parent().unwrap()).unwrap();std::fs::write(path.with_extension("tmp-abandoned"),b"partial").unwrap();let value=prepare_with(&file,&bake,||Ok(sample())).unwrap();assert!(value.path.is_some());cleanup(dir);}
}
