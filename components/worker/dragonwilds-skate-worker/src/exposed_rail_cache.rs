//! Optional exact cache of the native polylines built from a verified DWE snapshot.
//! This module never prepares geometry. Missing/rejected files must fall back to
//! rebuilding rails from that same verified snapshot, preserving their order.
use crate::exposed_edge_cache::Snapshot;
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{BufReader, BufWriter, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};

type Rails = Vec<Vec<[f32; 3]>>;
const HEADER: usize = 128;
const VERSION: u32 = 1;
const MAX_SEGMENTS: u64 = 50_000_000;
const MAX_STATS: usize = 1024 * 1024;
const POINT_CHUNK: usize = 4096;

#[derive(Debug, Clone, Serialize)]
pub struct CacheRecord {
    pub schema: u32,
    pub ready: bool,
    pub format: &'static str,
    pub path: PathBuf,
    pub bytes: u64,
    pub sha256: String,
    pub manifest_sha256: String,
    pub algorithm_sha256: String,
    pub rails: u64,
    pub points: u64,
    pub segments: u64,
    pub cache_hit: bool,
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|v| format!("{v:02x}")).collect()
}
fn pin(text: &str) -> Result<[u8; 32], String> {
    if text.len() != 64
        || !text
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
    {
        return Err("Invalid native rail cache identity".into());
    }
    let mut result = [0; 32];
    for (i, value) in result.iter_mut().enumerate() {
        *value = u8::from_str_radix(&text[i * 2..i * 2 + 2], 16).unwrap();
    }
    Ok(result)
}
fn location(snapshot: &Snapshot, create: bool) -> Result<PathBuf, String> {
    pin(&snapshot.manifest_sha256)?;
    pin(&snapshot.manifest.algorithm_sha256)?;
    if snapshot.manifest.segments as u64 > MAX_SEGMENTS {
        return Err("Native rail cache source segment limit".into());
    }
    let root = snapshot
        .path
        .parent()
        .ok_or("Missing DWE parent")?
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let folder = root.join("native-rails");
    if create {
        fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
    }
    if folder.try_exists().map_err(|e| e.to_string())?
        && folder.canonicalize().map_err(|e| e.to_string())? != folder
    {
        return Err("Native rail cache directory is redirected".into());
    }
    let path = folder.join(format!("{}.dwr1", snapshot.manifest_sha256));
    if path.try_exists().map_err(|e| e.to_string())? {
        if !fs::symlink_metadata(&path)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_file()
            || path.canonicalize().map_err(|e| e.to_string())? != path
        {
            return Err("Native rail cache file is redirected or not regular".into());
        }
    }
    Ok(path)
}
fn record_path(snapshot: &Snapshot, verified: &Path) -> Result<PathBuf, String> {
    // canonicalize() intentionally remains the internal containment check, but
    // on Windows it introduces a verbatim prefix. Public metadata must retain
    // the same ordinary path spelling as the DWE, which Python validates.
    let root = std::path::absolute(snapshot.path.parent().ok_or("Missing DWE parent")?)
        .map_err(|e| e.to_string())?;
    let public = root
        .join("native-rails")
        .join(format!("{}.dwr1", snapshot.manifest_sha256));
    if public.canonicalize().map_err(|e| e.to_string())? != verified {
        return Err("Native rail record does not name its verified cache".into());
    }
    Ok(public)
}
fn counts(snapshot: &Snapshot, rails: u64, points: u64, stats: u64) -> Result<u64, String> {
    let source = snapshot.manifest.segments as u64;
    if source > MAX_SEGMENTS
        || rails > source
        || points > source + rails
        || points < rails * 2
        || stats == 0
        || stats > MAX_STATS as u64
    {
        return Err("Native rail cache counts outside source bounds".into());
    }
    Ok(HEADER as u64 + stats + rails * 8 + points * 12)
}
fn check_stats(stats: &Value, rails: u64, points: u64) -> Result<(), String> {
    if !stats.is_object()
        || stats.get("provider_segments").and_then(Value::as_u64) != Some(points - rails)
    {
        return Err("Native rail cache statistics disagree with polylines".into());
    }
    Ok(())
}
fn point_valid(point: &[f32; 3], previous: Option<&[f32; 3]>) -> bool {
    point.iter().all(|v| v.is_finite() && v.abs() < 1e7) && previous.is_none_or(|p| p != point)
}

/// A missing cache is normal; every other rejection is an error for the caller
/// to log before reconstructing from the already verified DWE cells.
pub fn load(snapshot: &Snapshot) -> Result<Option<(Rails, Value)>, String> {
    Ok(load_with_record(snapshot)?.map(|(rails, stats, _)| (rails, stats)))
}

/// The record pins the exact file for copying the warm cache with a release or
/// an offline proof. Its full hash is computed during the same payload read.
pub fn load_with_record(
    snapshot: &Snapshot,
) -> Result<Option<(Rails, Value, CacheRecord)>, String> {
    let path = location(snapshot, false)?;
    let file = match File::open(&path) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.to_string()),
    };
    let bytes = file.metadata().map_err(|e| e.to_string())?.len();
    let mut input = BufReader::with_capacity(256 * 1024, file);
    let mut header = [0; HEADER];
    input.read_exact(&mut header).map_err(|e| e.to_string())?;
    let mut full_hash = Sha256::new();
    full_hash.update(header);
    let count = u64::from_le_bytes(header[72..80].try_into().unwrap());
    let points = u64::from_le_bytes(header[80..88].try_into().unwrap());
    let stats_len = u64::from_le_bytes(header[88..96].try_into().unwrap());
    if &header[..4] != b"DWR1"
        || header[4..8] != VERSION.to_le_bytes()
        || header[8..40] != pin(&snapshot.manifest.algorithm_sha256)?
        || header[40..72] != pin(&snapshot.manifest_sha256)?
        || counts(snapshot, count, points, stats_len)? != bytes
    {
        return Err("Native rail cache version, identity or size mismatch".into());
    }
    let mut payload_hash = Sha256::new();
    let mut stats_bytes = vec![0; stats_len as usize];
    input
        .read_exact(&mut stats_bytes)
        .map_err(|e| e.to_string())?;
    payload_hash.update(&stats_bytes);
    full_hash.update(&stats_bytes);
    let stats: Value = serde_json::from_slice(&stats_bytes).map_err(|e| e.to_string())?;
    check_stats(&stats, count, points)?;
    let mut rails = Vec::new();
    rails
        .try_reserve_exact(count as usize)
        .map_err(|e| e.to_string())?;
    let mut consumed = 0u64;
    let mut buffer = [0u8; POINT_CHUNK * 12];
    for _ in 0..count {
        let mut length = [0; 8];
        input.read_exact(&mut length).map_err(|e| e.to_string())?;
        payload_hash.update(length);
        full_hash.update(length);
        let n = u64::from_le_bytes(length);
        if n < 2 || n > points - consumed {
            return Err("Invalid native rail length".into());
        }
        let mut rail = Vec::new();
        rail.try_reserve_exact(n as usize)
            .map_err(|e| e.to_string())?;
        while rail.len() < n as usize {
            let chunk = (n as usize - rail.len()).min(POINT_CHUNK);
            let raw = &mut buffer[..chunk * 12];
            input.read_exact(raw).map_err(|e| e.to_string())?;
            payload_hash.update(&*raw);
            full_hash.update(&*raw);
            for row in raw.chunks_exact(12) {
                let point = std::array::from_fn(|i| {
                    f32::from_le_bytes(row[i * 4..i * 4 + 4].try_into().unwrap())
                });
                if !point_valid(&point, rail.last()) {
                    return Err("Invalid native rail point/primitive".into());
                }
                rail.push(point);
            }
        }
        consumed += n;
        rails.push(rail);
    }
    let mut tail = [0];
    if consumed != points
        || input.read(&mut tail).map_err(|e| e.to_string())? != 0
        || payload_hash.finalize().as_slice() != &header[96..128]
    {
        return Err("Native rail cache checksum/count mismatch".into());
    }
    let record = CacheRecord {
        schema: 1,
        ready: true,
        format: "exposed_native_rails_v1",
        path: record_path(snapshot, &path)?,
        bytes,
        sha256: hex(&full_hash.finalize()),
        manifest_sha256: snapshot.manifest_sha256.clone(),
        algorithm_sha256: snapshot.manifest.algorithm_sha256.clone(),
        rails: count,
        points,
        segments: points - count,
        cache_hit: true,
    };
    Ok(Some((rails, stats, record)))
}

/// Publish only a complete, flushed file. Only statistics are serialized to a
/// temporary Vec; point data uses a fixed-size buffer with no full payload copy.
pub fn store(
    snapshot: &Snapshot,
    rails: &[Vec<[f32; 3]>],
    stats: &Value,
) -> Result<CacheRecord, String> {
    let stats_bytes = serde_json::to_vec(stats).map_err(|e| e.to_string())?;
    let point_count = rails
        .iter()
        .try_fold(0u64, |n, rail| n.checked_add(rail.len() as u64))
        .ok_or("Native rail point count overflow")?;
    let count = rails.len() as u64;
    let bytes = counts(snapshot, count, point_count, stats_bytes.len() as u64)?;
    check_stats(stats, count, point_count)?;
    for rail in rails {
        if rail.len() < 2
            || rail
                .iter()
                .enumerate()
                .any(|(i, point)| !point_valid(point, i.checked_sub(1).map(|n| &rail[n])))
        {
            return Err("Invalid native rail point/primitive".into());
        }
    }
    let path = location(snapshot, true)?;
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let temporary = path.with_extension(format!(
        "{}.{}.{}.tmp",
        std::process::id(),
        nonce,
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|e| e.to_string())?;
    let result = (|| {
        let mut header = [0u8; HEADER];
        header[..4].copy_from_slice(b"DWR1");
        header[4..8].copy_from_slice(&VERSION.to_le_bytes());
        header[8..40].copy_from_slice(&pin(&snapshot.manifest.algorithm_sha256)?);
        header[40..72].copy_from_slice(&pin(&snapshot.manifest_sha256)?);
        header[72..80].copy_from_slice(&count.to_le_bytes());
        header[80..88].copy_from_slice(&point_count.to_le_bytes());
        header[88..96].copy_from_slice(&(stats_bytes.len() as u64).to_le_bytes());
        file.write_all(&header).map_err(|e| e.to_string())?;
        let mut payload_hash = Sha256::new();
        {
            let mut output = BufWriter::with_capacity(256 * 1024, &mut file);
            output.write_all(&stats_bytes).map_err(|e| e.to_string())?;
            payload_hash.update(&stats_bytes);
            let mut buffer = [0; POINT_CHUNK * 12];
            for rail in rails {
                let n = (rail.len() as u64).to_le_bytes();
                output.write_all(&n).map_err(|e| e.to_string())?;
                payload_hash.update(n);
                for chunk in rail.chunks(POINT_CHUNK) {
                    let raw = &mut buffer[..chunk.len() * 12];
                    for (slot, point) in raw.chunks_exact_mut(12).zip(chunk) {
                        for (axis, value) in point.iter().enumerate() {
                            slot[axis * 4..axis * 4 + 4].copy_from_slice(&value.to_le_bytes());
                        }
                    }
                    output.write_all(raw).map_err(|e| e.to_string())?;
                    payload_hash.update(&*raw);
                }
            }
            output.flush().map_err(|e| e.to_string())?;
        }
        header[96..128].copy_from_slice(&payload_hash.finalize());
        file.seek(SeekFrom::Start(0))
            .and_then(|_| file.write_all(&header))
            .and_then(|_| file.sync_all())
            .map_err(|e| e.to_string())?;
        if file.metadata().map_err(|e| e.to_string())?.len() != bytes {
            return Err("Native rail publication size mismatch".into());
        }
        file.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
        let mut full_hash = Sha256::new();
        let mut buffer = [0; 65536];
        loop {
            let n = file.read(&mut buffer).map_err(|e| e.to_string())?;
            if n == 0 {
                break;
            }
            full_hash.update(&buffer[..n]);
        }
        // Recheck destination containment after writing and before replacing a
        // missing/corrupt older cache. No original DWE or cell file is changed.
        if location(snapshot, true)? != path {
            return Err("Native rail cache path changed".into());
        }
        Ok(hex(&full_hash.finalize()))
    })();
    drop(file);
    let result = result.and_then(|sha256| {
        fs::rename(&temporary, &path).map_err(|e| e.to_string())?;
        Ok(CacheRecord {
            schema: 1,
            ready: true,
            format: "exposed_native_rails_v1",
            path: record_path(snapshot, &path)?,
            bytes,
            sha256,
            manifest_sha256: snapshot.manifest_sha256.clone(),
            algorithm_sha256: snapshot.manifest.algorithm_sha256.clone(),
            rails: count,
            points: point_count,
            segments: point_count - count,
            cache_hit: false,
        })
    });
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}
