//! Session-local ownership of the independently persisted grind graph. Live
//! edits move the graph rather than copying it; abandoned candidates recover
//! the accepted owner's exact persisted generation.
use crate::{
    exposed_edge_cache::Snapshot,
    grind_path_index::{self, CellInput, GrindPathIndex, Transaction},
};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
};

#[derive(Clone, Serialize)]
pub struct GraphCacheRecord {
    schema: u32,
    ready: bool,
    format: &'static str,
    pub path: PathBuf,
    bytes: u64,
    sha256: String,
    manifest_sha256: String,
    pub algorithm_sha256: String,
    extraction_algorithm_sha256: String,
    rails: u64,
    points: u64,
    segments: u64,
    cache_hit: bool,
}
impl GraphCacheRecord {
    pub fn value(&self) -> Result<Value, String> {
        serde_json::to_value(self).map_err(|e| e.to_string())
    }
}

fn hash_file(path: &Path) -> Result<(u64, String), String> {
    if !fs::symlink_metadata(path)
        .map_err(|e| e.to_string())?
        .file_type()
        .is_file()
    {
        return Err("Grind graph payload is not a regular immutable file".into());
    }
    let mut file = fs::File::open(path).map_err(|e| e.to_string())?;
    let size = file.metadata().map_err(|e| e.to_string())?.len();
    let mut hash = Sha256::new();
    let mut buffer = vec![0; 1024 * 1024];
    loop {
        let n = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    Ok((
        size,
        hash.finalize().iter().map(|b| format!("{b:02x}")).collect(),
    ))
}
fn is_pin(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn location(
    manifest: &Path,
    snapshot: &Snapshot,
    create: bool,
) -> Result<(PathBuf, String), String> {
    if !is_pin(&snapshot.manifest_sha256) || !is_pin(&snapshot.manifest.algorithm_sha256) {
        return Err("Invalid grind graph snapshot pin".into());
    }
    let algorithm = grind_path_index::algorithm(&snapshot.manifest.algorithm_sha256);
    let root = manifest
        .parent()
        .and_then(Path::parent)
        .ok_or("Missing map-data graph parent")?;
    let root = std::path::absolute(root).map_err(|e|e.to_string())?;
    let canonical_root = root.canonicalize().map_err(|e|e.to_string())?;
    let folder = root.join("incremental-grind-v1").join(&algorithm);
    if create {
        fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
    }
    if folder.try_exists().map_err(|e| e.to_string())?
        && folder.canonicalize().map_err(|e| e.to_string())? != canonical_root.join("incremental-grind-v1").join(&algorithm)
    {
        return Err("Grind graph cache folder is redirected".into());
    }
    Ok((
        folder.join(format!("{}.dgi1", snapshot.manifest_sha256)),
        algorithm,
    ))
}
pub fn inputs(snapshot: &Snapshot) -> Result<Vec<CellInput<'_>>, String> {
    if snapshot.manifest.cells.len() != snapshot.cells.len() {
        return Err("Grind graph snapshot cell census mismatch".into());
    }
    Ok(snapshot
        .manifest
        .cells
        .iter()
        .zip(&snapshot.cells)
        .map(|(cell, segments)| CellInput {
            key: cell.key,
            identity: &cell.sha256,
            segments,
        })
        .collect())
}
pub fn record(
    path: &Path,
    snapshot: &Snapshot,
    index: &GrindPathIndex,
    algorithm: &str,
    cache_hit: bool,
) -> Result<GraphCacheRecord, String> {
    if index.snapshot_identity() != snapshot.manifest_sha256 {
        return Err("Grind graph record snapshot mismatch".into());
    }
    let (bytes, sha256) = hash_file(path)?;
    let rails = index.rail_count() as u64;
    let segments = index.stats()["provider_segments"]
        .as_u64()
        .ok_or("Missing grind graph segment census")?;
    Ok(GraphCacheRecord {
        schema: 1,
        ready: true,
        format: "incremental_grind_v1",
        path: path.to_path_buf(),
        bytes,
        sha256,
        manifest_sha256: snapshot.manifest_sha256.clone(),
        algorithm_sha256: algorithm.into(),
        extraction_algorithm_sha256: snapshot.manifest.algorithm_sha256.clone(),
        rails,
        points: segments
            .checked_add(rails)
            .ok_or("Grind graph point count overflow")?,
        segments,
        cache_hit,
    })
}
pub fn load(
    manifest: &Path,
    snapshot: &Snapshot,
) -> Result<(GrindPathIndex, GraphCacheRecord), String> {
    let (path, algorithm) = location(manifest, snapshot, false)?;
    let index = GrindPathIndex::load(&path, &snapshot.manifest_sha256, &algorithm)?;
    let record = record(&path, snapshot, &index, &algorithm, true)?;
    Ok((index, record))
}
pub fn build(
    manifest: &Path,
    snapshot: &Snapshot,
) -> Result<(GrindPathIndex, GraphCacheRecord), String> {
    let (path, algorithm) = location(manifest, snapshot, true)?;
    let index = GrindPathIndex::build(snapshot.manifest_sha256.clone(), &inputs(snapshot)?)?;
    // Canonical payloads are immutable, including when another prepared run
    // published the same verified topology between lookup and this build.
    if path.try_exists().map_err(|e| e.to_string())? {
        return load(manifest, snapshot);
    }
    index.store(&path, &algorithm)?;
    let record = record(&path, snapshot, &index, &algorithm, false)?;
    Ok((index, record))
}
pub fn delta_location(
    manifest: &Path,
    snapshot: &Snapshot,
    parent: &GraphCacheRecord,
) -> Result<PathBuf, String> {
    let (path, algorithm) = location(manifest, snapshot, true)?;
    if algorithm != parent.algorithm_sha256 {
        return Err("Grind graph algorithm changed during a live scene update".into());
    }
    if !path.try_exists().map_err(|e| e.to_string())? {
        return Ok(path);
    }
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let mut hash = Sha256::new();
    hash.update(parent.sha256.as_bytes());
    hash.update(snapshot.manifest_sha256.as_bytes());
    hash.update(std::process::id().to_le_bytes());
    hash.update(NEXT.fetch_add(1, Ordering::Relaxed).to_le_bytes());
    hash.update(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|e| e.to_string())?
            .as_nanos()
            .to_le_bytes(),
    );
    let nonce: String = hash.finalize().iter().map(|b| format!("{b:02x}")).collect();
    Ok(path.with_file_name(format!("{}-{nonce}.dgi1", snapshot.manifest_sha256)))
}

#[derive(Clone)]
pub struct GraphState {
    index: Arc<Mutex<Option<GrindPathIndex>>>,
    leased: Arc<AtomicBool>,
    pub cache: GraphCacheRecord,
    initial_binding: bool,
}
impl GraphState {
    pub fn new(index: GrindPathIndex, cache: GraphCacheRecord, initial_binding: bool) -> Self {
        Self {
            index: Arc::new(Mutex::new(Some(index))),
            leased: Arc::new(AtomicBool::new(false)),
            cache,
            initial_binding,
        }
    }
    pub fn take(&self, expected: &str) -> Result<GraphLease<'_>, String> {
        if self
            .leased
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err("Grind graph is already leased by a scene update".into());
        }
        let result = (|| {
            let held = self
                .index
                .lock()
                .map_err(|_| "Grind graph ownership lock poisoned")?
                .take();
            let index = match held {
                Some(index) => index,
                None => {
                    let (bytes, sha256) = hash_file(&self.cache.path)?;
                    if bytes != self.cache.bytes || sha256 != self.cache.sha256 {
                        return Err("Accepted grind graph recovery payload changed".into());
                    }
                    let mut index = GrindPathIndex::load(
                        &self.cache.path,
                        expected,
                        &self.cache.algorithm_sha256,
                    )?;
                    if self.initial_binding {
                        index.rebind_initial_owners()?;
                    }
                    index
                }
            };
            if index.snapshot_identity() != expected {
                *self
                    .index
                    .lock()
                    .map_err(|_| "Grind graph ownership lock poisoned")? = Some(index);
                return Err("Accepted grind graph identity mismatch".into());
            }
            Ok(GraphLease {
                state: self,
                index: Some(index),
                pending: None,
            })
        })();
        if result.is_err() {
            self.leased.store(false, Ordering::Release);
        }
        result
    }
}
pub struct GraphLease<'a> {
    state: &'a GraphState,
    pub index: Option<GrindPathIndex>,
    pub pending: Option<Transaction>,
}
impl GraphLease<'_> {
    pub fn detach(mut self) -> Result<GrindPathIndex, String> {
        if self.pending.is_some() {
            return Err("Cannot transfer a pending grind graph".into());
        }
        self.index.take().ok_or("Missing leased grind graph".into())
    }
}
impl Drop for GraphLease<'_> {
    fn drop(&mut self) {
        let mut valid = true;
        if let Some(transaction) = self.pending.take() {
            if let Some(index) = &mut self.index {
                if let Err(error) = index.rollback(transaction) {
                    valid = false;
                    eprintln!("WORLD_GRIND_ROLLBACK_ERROR {error}");
                }
            }
        }
        if valid {
            if let Some(index) = self.index.take() {
                match self.state.index.lock() {
                    Ok(mut slot) => *slot = Some(index),
                    Err(_) => eprintln!("WORLD_GRIND_RETURN_ERROR poisoned ownership lock"),
                }
            }
        }
        self.state.leased.store(false, Ordering::Release);
    }
}

#[cfg(test)]
#[path = "world_runtime_graph_tests.rs"]
mod tests;
