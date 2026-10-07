//! Load-only compatibility reader for immutable original cache formats.
//! Startup fails with an offline-repair prerequisite instead of recomputing a
//! global rail merge when a derived or raw cache is missing or damaged.
use crate::{
    compact_geometry::CompactGeometry,
    compact_rails::{self, LipBake},
    merged_rail_cache,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    fs,
    io::{BufReader, Read},
    path::{Path, PathBuf},
    time::Instant,
};
const VERSION: u32 = 1;
const HEADER: usize = 88;
const SCHEMA: u32 = 1;
pub struct Restored {
    pub bake: LipBake,
    pub rails: Vec<Vec<[f32; 3]>>,
    pub checkpoint: Value,
}
fn limits(bake: &LipBake) -> Result<(usize, usize), String> {
    Ok((
        bake.lips.len(),
        bake.lips
            .len()
            .checked_mul(2)
            .ok_or("Merged rail limit overflow")?,
    ))
}
fn read_merged(
    path: &Path,
    identity: &[u8; 32],
    bake: &LipBake,
) -> Result<Vec<Vec<[f32; 3]>>, String> {
    let file = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let size = file.metadata().map_err(|e| e.to_string())?.len();
    let mut file = BufReader::with_capacity(1024 * 1024, file);
    let mut header = [0u8; HEADER];
    file.read_exact(&mut header).map_err(|e| e.to_string())?;
    let u64_at = |i| u64::from_le_bytes(header[i..i + 8].try_into().unwrap());
    if &header[..4] != b"S3R1"
        || u32::from_le_bytes(header[4..8].try_into().unwrap()) != VERSION
        || &header[24..56] != identity
    {
        return Err("Merged rail identity/version mismatch".into());
    }
    let count = usize::try_from(u64_at(8)).map_err(|_| "Merged rail count overflow")?;
    let points = usize::try_from(u64_at(16)).map_err(|_| "Merged point count overflow")?;
    let (max_rails, max_points) = limits(bake)?;
    let expected = (HEADER as u64)
        .checked_add(
            (count as u64)
                .checked_mul(8)
                .ok_or("Merged size overflow")?,
        )
        .and_then(|v| v.checked_add((points as u64).checked_mul(12)?))
        .ok_or("Merged size overflow")?;
    if count > max_rails
        || points > max_points
        || points < count.checked_mul(2).ok_or("Merged minimum overflow")?
        || size != expected
    {
        return Err("Invalid merged rail count/size".into());
    }
    let mut hash = blake3::Hasher::new();
    hash.update(&header[..56]);
    let mut rails = Vec::with_capacity(count);
    let mut consumed = 0usize;
    for _ in 0..count {
        let mut bytes = [0u8; 8];
        file.read_exact(&mut bytes).map_err(|e| e.to_string())?;
        hash.update(&bytes);
        let len =
            usize::try_from(u64::from_le_bytes(bytes)).map_err(|_| "Merged polyline overflow")?;
        if len < 2 || len > points - consumed {
            return Err("Invalid merged polyline boundary".into());
        }
        let mut rail = Vec::with_capacity(len);
        for _ in 0..len {
            let mut bytes = [0u8; 12];
            file.read_exact(&mut bytes).map_err(|e| e.to_string())?;
            hash.update(&bytes);
            let point = std::array::from_fn(|i| {
                f32::from_le_bytes(bytes[i * 4..i * 4 + 4].try_into().unwrap())
            });
            if !point.iter().all(|x| x.is_finite()) {
                return Err("Non-finite merged point".into());
            }
            rail.push(point);
        }
        consumed += len;
        rails.push(rail);
    }
    if consumed != points || hash.finalize().as_bytes() != &header[56..88] {
        return Err("Merged rail checksum/count mismatch".into());
    }
    Ok(rails)
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    schema: u32,
    base_fingerprint: String,
    scene_fingerprint: String,
    algorithm: String,
    payload: String,
    identity: String,
    candidates: usize,
    lips: usize,
    merged_file: String,
}

fn hex(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn folder(scene: &Path) -> Result<PathBuf, String> {
    Ok(scene
        .parent()
        .and_then(Path::parent)
        .ok_or("Scene manifest has no cache parent")?
        .join("accepted-scene-cache"))
}
fn algorithm() -> String {
    let mut hash = blake3::Hasher::new();
    hash.update(b"Dragonwilds accepted incremental scene v1\0");
    for bytes in [
        include_bytes!("compact_rails.rs").as_slice(),
        include_bytes!("compact_geometry.rs").as_slice(),
        include_bytes!("compact_world.rs").as_slice(),
        include_bytes!("accepted_scene_cache.rs").as_slice(),
        include_bytes!("merged_rail_cache.rs").as_slice(),
        include_bytes!("../Cargo.lock").as_slice(),
        include_bytes!("../../skate3-mashup/crates/render_anim/src/skate/rails.rs").as_slice(),
    ] {
        hash.update(&(bytes.len() as u64).to_le_bytes());
        hash.update(bytes);
    }
    hash.finalize().to_hex().to_string()
}
fn identity(base: &str, scene: &str, algorithm: &str, payload: &str) -> String {
    let mut hash = blake3::Hasher::new();
    hash.update(b"Dragonwilds exact accepted scene checkpoint v1\0");
    for value in [base, scene, algorithm, payload] {
        hash.update(&(value.len() as u64).to_le_bytes());
        hash.update(value.as_bytes());
    }
    hash.finalize().to_hex().to_string()
}
fn checkpoint(path: &Path, record: &Record) -> Value {
    json!({"schema":SCHEMA,"durable":true,
    "format":"accepted_incremental_v1","scene_fingerprint":record.scene_fingerprint,"checkpoint_file":path})
}
fn scene_identity(source: &CompactGeometry) -> Result<&str, String> {
    if source.anchor != [0.; 3] {
        return Err("Accepted scene cache requires fixed origin".into());
    }
    let scene = source
        .world
        .scene_fingerprint
        .as_deref()
        .ok_or("Accepted scene cache requires an overlay")?;
    if !hex(scene) || !hex(&source.world.source_fingerprint) {
        return Err("Invalid accepted scene identity".into());
    }
    Ok(scene)
}
pub fn merged(manifest: &Path, bake: &LipBake) -> Result<Vec<Vec<[f32; 3]>>, String> {
    let identity = merged_rail_cache::key(bake);
    let root = manifest.parent().ok_or("Merged manifest has no parent")?;
    let path = root
        .join("merged-rails-cache")
        .join(format!("{}.rails", blake3::Hash::from(identity).to_hex()));
    read_merged(&path, &identity, bake)
        .map_err(|e| format!("Legacy merged rails need offline repair: {e}"))
}
pub fn accepted(scene: &Path, source: &CompactGeometry) -> Result<Restored, String> {
    let started = Instant::now();
    let scene_id = scene_identity(source)?;
    let root = folder(scene)?;
    let marker = root.join(format!("{scene_id}.json"));
    if fs::metadata(&marker).map_err(|e| e.to_string())?.len() > 8192 {
        return Err("Oversized accepted scene marker".into());
    }
    let record: Record = serde_json::from_slice(&fs::read(&marker).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    if record.schema != SCHEMA
        || record.base_fingerprint != source.world.source_fingerprint
        || record.scene_fingerprint != scene_id
        || record.algorithm != algorithm()
        || !hex(&record.payload)
        || !hex(&record.identity)
        || record.identity
            != identity(
                &record.base_fingerprint,
                scene_id,
                &record.algorithm,
                &record.payload,
            )
    {
        return Err("Accepted scene checkpoint identity mismatch".into());
    }
    let bake = compact_rails::load_with_identity(
        &root.join(format!("{}.lips", record.identity)),
        &source.world,
        &record.identity,
    )?;
    if bake.candidates != record.candidates
        || bake.lips.len() != record.lips
        || compact_rails::payload_digest(&bake).to_hex().as_str() != record.payload
    {
        return Err("Accepted scene checkpoint payload mismatch".into());
    }
    let expected = format!(
        "{}.rails",
        blake3::Hash::from(merged_rail_cache::key(&bake)).to_hex()
    );
    if record.merged_file != expected {
        return Err("Accepted scene merged identity mismatch".into());
    }
    let rails = merged(&root.join("manifest.json"), &bake)?;
    eprintln!(
        "WORLD_ACCEPTED_SCENE_CACHE hit lips={} rails={} elapsed_ms={}",
        bake.lips.len(),
        rails.len(),
        started.elapsed().as_millis()
    );
    Ok(Restored {
        bake,
        rails,
        checkpoint: checkpoint(&marker, &record),
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn startup_reader_never_creates_or_repairs_cache() {
        let root = std::env::temp_dir().join(format!("dw-readonly-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let file = root.join("manifest.json");
        let bake = LipBake {
            fingerprint: "fixture".into(),
            candidates: 0,
            lips: vec![],
        };
        assert!(merged(&file, &bake).is_err());
        assert!(!root.join("merged-rails-cache").exists());
        let ready = merged_rail_cache::prepare(&file, &bake).unwrap();
        assert!(merged(&file, &bake).unwrap().is_empty());
        let path = ready.path.unwrap();
        std::fs::write(&path, b"broken").unwrap();
        assert!(merged(&file, &bake).is_err());
        assert_eq!(std::fs::read(&path).unwrap(), b"broken");
        std::fs::remove_file(&path).unwrap();
        std::fs::remove_dir(root.join("merged-rails-cache")).unwrap();
        std::fs::remove_dir(&root).unwrap();
    }
}
