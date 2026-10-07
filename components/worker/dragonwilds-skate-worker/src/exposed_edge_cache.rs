//! Independently versioned exposed-edge payloads. The original world/lip caches
//! are never edited. A complete manifest is published only after every cell is
//! verified; scene updates reuse cells whose recorded read dependencies did not
//! intersect either the old or new changed geometry.
use crate::{
    compact_world::{Bounds, CompactWorld},
    exposed_edges::{self, Config, Segment},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeSet, HashMap, HashSet},
    fs,
    io::{BufRead, BufReader, Read, Seek, SeekFrom, Write},
    path::{Component, Path, PathBuf},
    sync::Arc,
    time::Instant,
};

const CELL: f64 = 3200.;
const HALO: f64 = 100.;
const MAX_CELLS: usize = 2_000_000;
const MAX_SEGMENTS: usize = 50_000_000;
type Key = [i32; 3];
type Triangle = [[f64; 3]; 3];
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Parameters {
    pub cell_size_cm: f64,
    pub halo_cm: f64,
    pub truck_distance_cm: f64,
    pub tolerance_cm: f64,
}
impl Parameters {
    pub fn load(assets: &Path) -> Result<Self, String> {
        let data = skate_data::collections::Collections::load(assets)?;
        let truck = f64::from(data.float("physics_grinds", "default", "DeckCenterToTruck")?) * 100.;
        if !truck.is_finite() || truck <= 0. || truck * 2.1 > HALO {
            return Err("Invalid original grind probe extent".into());
        }
        Ok(Self {
            cell_size_cm: CELL,
            halo_cm: HALO,
            truck_distance_cm: truck,
            tolerance_cm: 1e-5,
        })
    }
    fn config(&self) -> Config {
        Config {
            truck_distance_cm: self.truck_distance_cm,
            tolerance_cm: self.tolerance_cm,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Region {
    pub min: [f64; 3],
    pub max: [f64; 3],
}
impl From<Bounds> for Region {
    fn from(v: Bounds) -> Self {
        Self {
            min: v.min,
            max: v.max,
        }
    }
}
impl Region {
    fn bound(&self) -> Bounds {
        Bounds {
            min: self.min,
            max: self.max,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Cell {
    pub key: Key,
    pub min: [f64; 3],
    pub max: [f64; 3],
    pub dependencies: Vec<Region>,
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
    pub segments: usize,
    pub census: Value,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub magic: String,
    pub schema: u32,
    pub complete: bool,
    pub base_fingerprint: String,
    pub scene_fingerprint: Option<String>,
    pub algorithm_sha256: String,
    pub parameters: Parameters,
    pub cells: Vec<Cell>,
    pub segments: usize,
}
#[derive(Clone)]
pub struct Snapshot {
    pub manifest: Manifest,
    pub path: PathBuf,
    pub manifest_sha256: String,
    pub cells: Vec<Arc<Vec<Segment>>>,
    pub stats: Value,
}
fn digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
fn hex(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn finite(b: Bounds) -> bool {
    (0..3).all(|i| b.min[i].is_finite() && b.max[i].is_finite() && b.min[i] <= b.max[i])
}
fn overlap(a: Bounds, b: Bounds) -> bool {
    (0..3).all(|i| a.min[i] <= b.max[i] && b.min[i] <= a.max[i])
}
fn expand(b: Bounds, pad: f64) -> Bounds {
    Bounds {
        min: b.min.map(|v| v - pad),
        max: b.max.map(|v| v + pad),
    }
}
fn includes(a: Bounds, b: Bounds) -> bool {
    (0..3).all(|i| a.min[i] <= b.min[i] && a.max[i] >= b.max[i])
}
fn bound(key: Key) -> Bounds {
    Bounds {
        min: key.map(|v| f64::from(v) * CELL),
        max: key.map(|v| (f64::from(v) + 1.) * CELL),
    }
}
fn cell_key(p: [f64; 3]) -> Result<Key, String> {
    if p.iter()
        .any(|v| !v.is_finite() || v.abs() > f64::from(i32::MAX - 2) * CELL)
    {
        return Err("Exposed-edge cell coordinate overflow".into());
    }
    Ok(p.map(|v| (v / CELL).floor() as i32))
}
pub fn algorithm(parameters: &Parameters) -> String {
    let mut hash = Sha256::new();
    hash.update(b"Dragonwild exposed edges v1\0");
    for bytes in [include_bytes!("exposed_edge_cache.rs").as_slice(),include_bytes!("exposed_edges.rs").as_slice(),
        include_bytes!("exposed_query.rs").as_slice(),
        include_bytes!("exposed_rail_cache.rs").as_slice(),
        include_bytes!("compact_world.rs").as_slice(),include_bytes!("compact_geometry.rs").as_slice(),include_bytes!("../Cargo.lock").as_slice(),
        include_bytes!("../../skate3-mashup/skate/crates/skate-core/src/air/trajectory/grind_surface.rs").as_slice(),
        include_bytes!("../../skate3-mashup/skate/crates/skate-core/src/air/trajectory/grind_surface/classify.rs").as_slice()]{hash.update((bytes.len()as u64).to_le_bytes());hash.update(bytes);}
    hash.update(serde_json::to_vec(parameters).unwrap());
    hash.finalize().iter().map(|b| format!("{b:02x}")).collect()
}
fn location(
    base_manifest: &Path,
    world: &CompactWorld,
    parameters: &Parameters,
) -> Result<PathBuf, String> {
    let root = base_manifest
        .parent()
        .and_then(Path::parent)
        .ok_or("Base manifest lacks map-data parent")?;
    if !hex(&world.source_fingerprint) || world.scene_fingerprint.as_ref().is_some_and(|v| !hex(v))
    {
        return Err("Invalid exposed-edge world fingerprint".into());
    }
    Ok(root
        .join("exposed-edges-v1")
        .join(algorithm(parameters))
        .join(&world.source_fingerprint)
        .join(format!(
            "{}.json",
            world.scene_fingerprint.as_deref().unwrap_or("base")
        )))
}
fn relative(root: &Path, path: &str) -> Result<PathBuf, String> {
    if path.is_empty()
        || Path::new(path)
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
    {
        return Err("Invalid exposed-edge relative payload path".into());
    }
    let full = root.join(path).canonicalize().map_err(|e| e.to_string())?;
    if !full.starts_with(root.canonicalize().map_err(|e| e.to_string())?) {
        return Err("Exposed-edge payload escapes cache".into());
    }
    Ok(full)
}
fn encode(segments: &[Segment]) -> Result<Vec<u8>, String> {
    if segments.len() > MAX_SEGMENTS {
        return Err("Exposed-edge segment limit".into());
    }
    let mut bytes = Vec::with_capacity(16 + 48 * segments.len());
    bytes.extend(b"DWC1");
    bytes.extend(1u32.to_le_bytes());
    bytes.extend((segments.len() as u64).to_le_bytes());
    for segment in segments {
        for value in segment.a.into_iter().chain(segment.b) {
            if !value.is_finite() {
                return Err("Non-finite exposed segment".into());
            }
            bytes.extend(value.to_le_bytes());
        }
    }
    Ok(bytes)
}
fn decode(bytes: &[u8], count: usize) -> Result<Vec<Segment>, String> {
    if count > MAX_SEGMENTS
        || bytes.len() != 16 + count * 48
        || &bytes[..4] != b"DWC1"
        || bytes[4..8] != 1u32.to_le_bytes()
        || u64::from_le_bytes(bytes[8..16].try_into().unwrap()) != count as u64
    {
        return Err("Invalid DWC1 header/count/size".into());
    }
    bytes[16..]
        .chunks_exact(48)
        .map(|row| {
            let p: [f64; 6] = std::array::from_fn(|i| {
                f64::from_le_bytes(row[i * 8..i * 8 + 8].try_into().unwrap())
            });
            if p.iter().any(|v| !v.is_finite()) {
                return Err("Non-finite DWC1 endpoint".into());
            }
            let segment = Segment {
                a: [p[0], p[1], p[2]],
                b: [p[3], p[4], p[5]],
            };
            if segment.a == segment.b {
                return Err("Degenerate DWC1 segment".into());
            }
            Ok(segment)
        })
        .collect()
}
fn atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static N: AtomicU64 = AtomicU64::new(0);
    fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    let tmp = path.with_extension(format!(
        "tmp-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::Relaxed)
    ));
    let result = (|| {
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&tmp)
            .map_err(|e| e.to_string())?;
        file.write_all(bytes).map_err(|e| e.to_string())?;
        file.flush().map_err(|e| e.to_string())?;
        file.sync_all().map_err(|e| e.to_string())?;
        drop(file);
        fs::rename(&tmp, path).map_err(|e| e.to_string())
    })();
    if result.is_err() {
        let _ = fs::remove_file(tmp);
    }
    result
}
fn read_cell(root: &Path, cell: &Cell) -> Result<Vec<Segment>, String> {
    if cell.bytes != 16 + cell.segments as u64 * 48
        || !hex(&cell.sha256)
        || cell.path != format!("payloads/{}.edges", cell.sha256)
    {
        return Err("Invalid exposed cell identity".into());
    }
    let path = relative(root, &cell.path)?;
    if fs::metadata(&path).map_err(|e| e.to_string())?.len() != cell.bytes {
        return Err("Exposed cell size mismatch".into());
    }
    let mut bytes = Vec::new();
    fs::File::open(path)
        .map_err(|e| e.to_string())?
        .take(cell.bytes + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if digest(&bytes) != cell.sha256 {
        return Err("Exposed cell checksum mismatch".into());
    }
    decode(&bytes, cell.segments)
}
fn read_manifest(
    base: &Path,
    world: &CompactWorld,
    parameters: &Parameters,
) -> Result<(PathBuf, Manifest, String), String> {
    let path = location(base, world, parameters)?;
    let size = fs::metadata(&path).map_err(|e| e.to_string())?.len();
    if size > 512 * 1024 * 1024 {
        return Err("Oversized exposed-edge manifest".into());
    }
    let bytes = fs::read(&path).map_err(|e| e.to_string())?;
    let mut manifest: Manifest = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    // Compare the persisted decimal representation, not a parser-rounding bit.
    // The algorithm pin below is still computed from exact owned probe values.
    // Never round those values for actual extraction or source contact probes.
    let persisted_parameters: Parameters =
        serde_json::from_slice(&serde_json::to_vec(parameters).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
    if manifest.magic != "DWE1"
        || manifest.schema != 1
        || !manifest.complete
        || manifest.base_fingerprint != world.source_fingerprint
        || manifest.scene_fingerprint != world.scene_fingerprint
        || manifest.algorithm_sha256 != algorithm(parameters)
        || manifest.parameters != persisted_parameters
        || manifest.cells.len() > MAX_CELLS
        || manifest.segments > MAX_SEGMENTS
    {
        return Err("Exposed-edge manifest identity mismatch".into());
    }
    manifest.parameters = parameters.clone();
    let mut last = None;
    let mut count = 0usize;
    for cell in &manifest.cells {
        let expected = bound(cell.key);
        if last.is_some_and(|key| key >= cell.key)
            || cell.min != expected.min
            || cell.max != expected.max
            || cell.dependencies.is_empty()
            || cell.dependencies.iter().any(|r| !finite(r.bound()))
            || !cell
                .dependencies
                .iter()
                .any(|r| includes(r.bound(), expand(expected, HALO)))
        {
            return Err("Invalid exposed cell ownership/dependencies".into());
        }
        last = Some(cell.key);
        count = count
            .checked_add(cell.segments)
            .ok_or("Exposed count overflow")?;
    }
    if count != manifest.segments {
        return Err("Exposed manifest segment census mismatch".into());
    }
    let coverage = occupied(world)?;
    if coverage.len() != manifest.cells.len()
        || coverage
            .into_iter()
            .zip(&manifest.cells)
            .any(|(key, c)| key != c.key)
    {
        return Err("Exposed manifest does not cover the complete current world".into());
    }
    Ok((path, manifest, digest(&bytes)))
}
pub fn load(
    base: &Path,
    world: &CompactWorld,
    parameters: &Parameters,
) -> Result<Snapshot, String> {
    let started = Instant::now();
    let (path, manifest, sha) = read_manifest(base, world, parameters)?;
    let root = path.parent().unwrap();
    let mut payloads = HashMap::<(String, u64, usize), Arc<Vec<Segment>>>::new();
    let mut cells = Vec::with_capacity(manifest.cells.len());
    for cell in &manifest.cells {
        let key = (cell.sha256.clone(), cell.bytes, cell.segments);
        // Metadata is checked even on a memo hit; an alias path or inconsistent
        // record must not bypass the contained content-addressed payload rule.
        if cell.path != format!("payloads/{}.edges", cell.sha256)
            || cell.bytes != 16 + cell.segments as u64 * 48
            || !hex(&cell.sha256)
        {
            return Err("Invalid exposed payload memo identity".into());
        }
        let segments = if let Some(value) = payloads.get(&key) {
            value.clone()
        } else {
            let value = Arc::new(read_cell(root, cell)?);
            payloads.insert(key, value.clone());
            value
        };
        cells.push(segments);
    }
    let stats = json!({"cache_hit":true,"extraction_calls":0,"rebuilt_cells":0,"reused_cells":manifest.cells.len(),"elapsed_ms":started.elapsed().as_millis()});
    Ok(Snapshot {
        manifest,
        path,
        manifest_sha256: sha,
        cells,
        stats,
    })
}
/// Conservative output occupancy. Small groups retain their inexpensive full
/// bounds. Sparse, huge groups walk physical edges rather than filling millions
/// of empty volume cells. The kernel only splits original edges or lifts them
/// at most four centimetres; probe extents belong to read dependencies instead.
pub(crate) fn occupied(world: &CompactWorld) -> Result<BTreeSet<Key>, String> {
    let mut keys = BTreeSet::new();
    for group in world.groups() {
        if !finite(group.bounds) {
            return Err("Invalid source group bounds".into());
        }
        let padding = 4.
            + 16e-5
            + group
                .bounds
                .min
                .into_iter()
                .chain(group.bounds.max)
                .map(f64::abs)
                .fold(1., f64::max)
                * f64::EPSILON
                * 64.;
        let coverage = expand(group.bounds, padding);
        let lo = cell_key(coverage.min)?;
        let hi = cell_key(coverage.max)?;
        let number = (0..3)
            .try_fold(1u64, |n, i| {
                n.checked_mul((i64::from(hi[i]) - i64::from(lo[i]) + 1) as u64)
            })
            .ok_or("Cell extent overflow")?;
        if number > 4096 && number > (group.triangle_range.len() as u64).saturating_mul(8) {
            for id in group.triangle_range.clone() {
                let triangle = world.raw_triangle(id);
                for edge in 0..3 {
                    edge_cells(triangle[edge], triangle[(edge + 1) % 3], &mut keys)?;
                }
            }
        } else {
            if number > MAX_CELLS as u64 {
                return Err("Source group exceeds exposed cell budget".into());
            }
            for x in lo[0]..=hi[0] {
                for y in lo[1]..=hi[1] {
                    for z in lo[2]..=hi[2] {
                        keys.insert([x, y, z]);
                    }
                }
            }
        }
        if keys.len() > MAX_CELLS {
            return Err("World exceeds exposed cell budget".into());
        }
    }
    Ok(keys)
}
fn edge_cells(a: [f64; 3], b: [f64; 3], keys: &mut BTreeSet<Key>) -> Result<(), String> {
    let mut at = cell_key(a)?;
    let end = cell_key(b)?;
    let d: [f64; 3] = std::array::from_fn(|i| b[i] - a[i]);
    let step = d.map(|v| {
        if v > 0. {
            1
        } else if v < 0. {
            -1
        } else {
            0
        }
    });
    let delta = d.map(|v| {
        if v == 0. {
            f64::INFINITY
        } else {
            CELL / v.abs()
        }
    });
    let mut next: [f64; 3] = std::array::from_fn(|i| {
        if step[i] > 0 {
            ((at[i] as f64 + 1.) * CELL - a[i]) / d[i]
        } else if step[i] < 0 {
            (at[i] as f64 * CELL - a[i]) / d[i]
        } else {
            f64::INFINITY
        }
    });
    let margin =
        4. + 16e-5 + a.into_iter().chain(b).map(f64::abs).fold(1., f64::max) * f64::EPSILON * 64.;
    let limit = (0..3)
        .map(|i| (i64::from(at[i]) - i64::from(end[i])).unsigned_abs())
        .sum::<u64>()
        + 4;
    for _ in 0..limit {
        for x in -1..=1 {
            for y in -1..=1 {
                for z in -1..=1 {
                    let k = [at[0] + x, at[1] + y, at[2] + z];
                    let box_ = expand(bound(k), margin);
                    let mut low = 0f64;
                    let mut high = 1f64;
                    let mut hit = true;
                    for i in 0..3 {
                        if d[i] == 0. {
                            if a[i] < box_.min[i] || a[i] > box_.max[i] {
                                hit = false;
                                break;
                            }
                        } else {
                            let u = (box_.min[i] - a[i]) / d[i];
                            let v = (box_.max[i] - a[i]) / d[i];
                            low = low.max(u.min(v));
                            high = high.min(u.max(v));
                            if low > high {
                                hit = false;
                                break;
                            }
                        }
                    }
                    if hit {
                        keys.insert(k);
                    }
                }
            }
        }
        if keys.len() > MAX_CELLS {
            return Err("World exceeds exposed cell budget".into());
        }
        if at == end {
            return Ok(());
        }
        let axis = (0..3).min_by(|&i, &j| next[i].total_cmp(&next[j])).unwrap();
        at[axis] += step[axis];
        next[axis] += delta[axis];
    }
    Err("Physical edge cell traversal did not reach its endpoint".into())
}
/// Resumable offline work is a separate journal, never a ready manifest.
/// Each record follows its immutable payload; a truncated last record is
/// discarded. Identity and payloads are independently checked when reused.
struct WorkJournal {
    path: PathBuf,
    file: fs::File,
    synced: Instant,
}
impl WorkJournal {
    fn open(
        path: &Path,
        world: &CompactWorld,
        p: &Parameters,
        keys: &BTreeSet<Key>,
    ) -> Result<(Self, HashMap<Key, Cell>), String> {
        let path = path.with_extension("incomplete.jsonl");
        let header = json!({"magic":"DWP1","schema":1,"complete":false,"base_fingerprint":world.source_fingerprint,"scene_fingerprint":world.scene_fingerprint,"algorithm_sha256":algorithm(p),"parameters":p});
        let mut header_bytes = serde_json::to_vec(&header).map_err(|e| e.to_string())?;
        header_bytes.push(b'\n');
        let mut records = HashMap::new();
        let mut valid = 0u64;
        if let Ok(file) = fs::File::open(&path) {
            if file.metadata().map_err(|e| e.to_string())?.len() <= 512 * 1024 * 1024 {
                let mut reader = BufReader::new(file);
                let mut line = String::new();
                let n = reader.read_line(&mut line).map_err(|e| e.to_string())?;
                if line.as_bytes() == header_bytes {
                    valid = n as u64;
                    loop {
                        line.clear();
                        let n = reader.read_line(&mut line).map_err(|e| e.to_string())?;
                        if n == 0 {
                            break;
                        }
                        if !line.ends_with('\n') || n > 16 * 1024 * 1024 {
                            break;
                        }
                        let Ok(c) = serde_json::from_str::<Cell>(&line) else {
                            break;
                        };
                        let b = bound(c.key);
                        if !keys.contains(&c.key)
                            || c.min != b.min
                            || c.max != b.max
                            || c.segments > MAX_SEGMENTS
                            || c.dependencies.is_empty()
                            || c.dependencies.iter().any(|r| !finite(r.bound()))
                            || !c
                                .dependencies
                                .iter()
                                .any(|r| includes(r.bound(), expand(b, HALO)))
                        {
                            break;
                        }
                        records.insert(c.key, c);
                        valid += n as u64;
                    }
                }
            }
        }
        fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
        if valid == 0 {
            atomic(&path, &header_bytes)?;
            valid = header_bytes.len() as u64;
        }
        let mut file = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(&path)
            .map_err(|e| e.to_string())?;
        file.set_len(valid).map_err(|e| e.to_string())?;
        file.seek(SeekFrom::End(0)).map_err(|e| e.to_string())?;
        Ok((
            Self {
                path,
                file,
                synced: Instant::now(),
            },
            records,
        ))
    }
    fn record(&mut self, record: &Cell) -> Result<(), String> {
        serde_json::to_writer(&mut self.file, record).map_err(|e| e.to_string())?;
        self.file.write_all(b"\n").map_err(|e| e.to_string())?;
        if self.synced.elapsed().as_secs() >= 5 {
            self.file.sync_data().map_err(|e| e.to_string())?;
            self.synced = Instant::now();
        }
        Ok(())
    }
    fn finish(self) {
        drop(self.file);
        let _ = fs::remove_file(self.path);
    }
}
// Workers derive immutable geometry only. All filesystem publication remains
// on the coordinator so failure cannot advertise a partially prepared world.
struct DerivedCell {
    record: Cell,
    segments: Arc<Vec<Segment>>,
    bytes: Vec<u8>,
}
#[cfg(test)]
pub(crate) fn profile_cell(
    world: &CompactWorld,
    parameters: &Parameters,
    key: [i32; 3],
) -> Result<(String, usize), String> {
    let derived = derive_cell(world, parameters, key)?;
    Ok((derived.record.sha256, derived.record.segments))
}
fn derive_cell(
    world: &CompactWorld,
    parameters: &Parameters,
    key: Key,
) -> Result<DerivedCell, String> {
    let owner = bound(key);
    let halo = expand(owner, HALO);
    let triangles: Vec<Triangle> = world
        .candidate_triangles(halo.min, halo.max)
        .into_iter()
        .map(|id| world.raw_triangle(id))
        .collect();
    let local = crate::exposed_query::LocalQuery::new(&triangles, halo);
    let mut dependencies = vec![Region::from(halo)];
    let mut invalid_query = false;
    let mut query = |region: Bounds| {
        if !finite(region) {
            invalid_query = true;
            return Vec::new();
        }
        dependencies.push(region.into());
        if let Some(value) = local.query(region) {
            return value;
        }
        world
            .candidate_triangles(region.min, region.max)
            .into_iter()
            .map(|id| world.raw_triangle(id))
            .collect::<Vec<_>>()
    };
    let patch = exposed_edges::derive(&triangles, owner, &mut query, &parameters.config())?;
    if invalid_query {
        return Err("Exposed-edge kernel requested invalid bounds".into());
    }
    for region in patch.dependencies {
        if !finite(region) {
            return Err("Invalid exposed kernel dependency".into());
        }
        dependencies.push(region.into());
    }
    // The whole input halo is always a dependency, even when a triangle
    // produces no edge. Adding/removing cover there must invalidate it.
    let mut union = dependencies[0].bound();
    for r in dependencies.iter().skip(1) {
        for i in 0..3 {
            union.min[i] = union.min[i].min(r.min[i]);
            union.max[i] = union.max[i].max(r.max[i]);
        }
    }
    // JSON's default fast f64 reader need not round-trip every decimal bit.
    // Integral centimetres are exact at supported world coordinates. Expand
    // outward by another centimetre so restored dependency boxes remain a
    // conservative superset even at a former fractional query boundary.
    union.min = union.min.map(|v| v.floor() - 1.);
    union.max = union.max.map(|v| v.ceil() + 1.);
    let bytes = encode(&patch.segments)?;
    let sha = digest(&bytes);
    let rel = format!("payloads/{sha}.edges");
    let cell = Cell {
        key,
        min: owner.min,
        max: owner.max,
        dependencies: vec![union.into()],
        path: rel,
        bytes: bytes.len() as u64,
        sha256: sha,
        segments: patch.segments.len(),
        census: serde_json::to_value(patch.census).map_err(|e| e.to_string())?,
    };
    Ok(DerivedCell {
        record: cell,
        segments: Arc::new(patch.segments),
        bytes,
    })
}

/// Explicit offline preparation, or a bounded delta from an already complete
/// accepted snapshot. Absence of a previous snapshot never means a runtime bake.
pub fn prepare(
    base: &Path,
    world: &CompactWorld,
    parameters: &Parameters,
    previous: Option<&Snapshot>,
    changes: &[Bounds],
) -> Result<Snapshot, String> {
    prepare_with_workers(base, world, parameters, previous, changes, 4, None)
}

#[cfg(test)]
pub(crate) fn prepare_testing(
    base: &Path,
    world: &CompactWorld,
    parameters: &Parameters,
    previous: Option<&Snapshot>,
    changes: &[Bounds],
    workers: usize,
    fail_after: Option<usize>,
) -> Result<Snapshot, String> {
    prepare_with_workers(
        base, world, parameters, previous, changes, workers, fail_after,
    )
}

fn prepare_with_workers(
    base: &Path,
    world: &CompactWorld,
    parameters: &Parameters,
    previous: Option<&Snapshot>,
    changes: &[Bounds],
    requested_workers: usize,
    fail_after: Option<usize>,
) -> Result<Snapshot, String> {
    match load(base, world, parameters) {
        Ok(cached) => return Ok(cached),
        Err(error) => eprintln!("WORLD_EXPOSED_CACHE unavailable: {error}"),
    }
    let started = Instant::now();
    let path = location(base, world, parameters)?;
    let root = path.parent().unwrap();
    let keys = occupied(world)?;
    let same = read_manifest(base, world, parameters).ok();
    let prior = previous.filter(|s| {
        s.manifest.base_fingerprint == world.source_fingerprint
            && s.manifest.algorithm_sha256 == algorithm(parameters)
    });
    if changes.iter().any(|&b| !finite(b)) {
        return Err("Invalid exposed scene difference bounds".into());
    }
    let by_key: HashMap<_, _> = prior
        .map(|s| {
            s.manifest
                .cells
                .iter()
                .enumerate()
                .map(|(i, c)| (c.key, i))
                .collect()
        })
        .unwrap_or_default();
    let mut repaired: HashMap<_, _> = same
        .as_ref()
        .map(|(_, m, _)| m.cells.iter().map(|c| (c.key, c.clone())).collect())
        .unwrap_or_default();
    let mut journal = if previous.is_none() && same.is_none() {
        let (journal, resume) = WorkJournal::open(&path, world, parameters, &keys)?;
        repaired.extend(resume);
        Some(journal)
    } else {
        None
    };
    let total = keys.len();
    let mut last_progress = Instant::now();
    eprintln!(
        "WORLD_EXPOSED_PROGRESS total={total} cells=0 rebuilt=0 reused=0 segments=0 elapsed_ms=0"
    );
    let mut records = Vec::with_capacity(keys.len());
    let mut cells = Vec::with_capacity(keys.len());
    let mut rebuilt = 0usize;
    let mut reused = 0usize;
    let mut segment_count = 0usize;
    let mut verified_payloads = HashSet::new();
    let mut repaired_payloads = 0usize;
    let mut work = Vec::new();
    let mut retained_cells = Vec::new();
    for key in keys {
        let mut retained = None;
        if let (Some(prior), Some(&i)) = (prior, by_key.get(&key)) {
            let c = &prior.manifest.cells[i];
            if !changes
                .iter()
                .any(|&b| c.dependencies.iter().any(|r| overlap(r.bound(), b)))
            {
                retained = Some((c.clone(), prior.cells[i].clone()));
            }
        }
        if retained.is_none() {
            if let Some(c) = repaired.get(&key) {
                if let Ok(segments) = read_cell(root, c) {
                    retained = Some((c.clone(), Arc::new(segments)));
                }
            }
        }
        if let Some(retained) = retained {
            retained_cells.push(retained);
        } else {
            work.push(key);
        }
    }
    // Cold offline work alone uses four readers. Live scene deltas and repair
    // of an existing manifest stay serial, keeping gameplay contention bounded.
    let workers = if previous.is_none() && same.is_none() {
        requested_workers.clamp(1, 4).min(work.len().max(1))
    } else {
        1
    };
    let mut publish = |record: Cell,
                       segments: Arc<Vec<Segment>>,
                       bytes: Option<Vec<u8>>|
     -> Result<(), String> {
        if fail_after.is_some_and(|n| records.len() >= n) {
            return Err("Injected incomplete preparation".into());
        }
        if let Some(bytes) = bytes {
            let payload = root.join(&record.path);
            if fs::read(&payload).ok().is_none_or(|old| old != bytes) {
                atomic(&payload, &bytes)?;
            }
            rebuilt += 1;
        } else {
            reused += 1;
        }
        // A retained in-memory cell may outlive loss/corruption of its payload.
        // Repair from verified memory before publishing durable readiness.
        if verified_payloads.insert((record.sha256.clone(), record.bytes, record.segments))
            && read_cell(root, &record).is_err()
        {
            let bytes = encode(&segments)?;
            if digest(&bytes) != record.sha256 || bytes.len() as u64 != record.bytes {
                return Err("Retained exposed cell memory identity changed".into());
            }
            atomic(&root.join(&record.path), &bytes)?;
            read_cell(root, &record)?;
            repaired_payloads += 1;
        }
        segment_count = segment_count
            .checked_add(record.segments)
            .ok_or("Exposed segment census overflow")?;
        if segment_count > MAX_SEGMENTS {
            return Err("Exposed world segment budget exceeded".into());
        }
        if let Some(journal) = journal.as_mut() {
            // Resume records were already durable; do not grow the journal with
            // duplicate rows after each interrupted attempt.
            if repaired.get(&record.key).is_none_or(|old| {
                serde_json::to_value(old).ok() != serde_json::to_value(&record).ok()
            }) {
                journal.record(&record)?;
            }
        }
        records.push(record);
        cells.push(segments);
        if records.len() % 100 == 0 || last_progress.elapsed().as_secs() >= 10 {
            last_progress = Instant::now();
            eprintln!(
                "WORLD_EXPOSED_PROGRESS total={total} cells={} rebuilt={rebuilt} reused={reused} segments={segment_count} elapsed_ms={}",
                records.len(),
                started.elapsed().as_millis()
            );
        }
        Ok(())
    };
    for (record, segments) in retained_cells {
        publish(record, segments, None)?;
    }
    if workers == 1 {
        for key in work {
            let d = derive_cell(world, parameters, key)?;
            publish(d.record, d.segments, Some(d.bytes))?;
        }
    } else {
        use std::sync::{
            atomic::{AtomicBool, AtomicUsize, Ordering},
            mpsc,
        };
        let cursor = AtomicUsize::new(0);
        let cancel = AtomicBool::new(false);
        // Four active jobs and four queued results: at most eight uncommitted
        // cell outputs. Completion order is irrelevant to final manifest order.
        let (tx, rx) = mpsc::sync_channel::<Result<DerivedCell, String>>(4);
        std::thread::scope(|scope| -> Result<(), String> {
            for _ in 0..workers {
                let tx = tx.clone();
                let work = &work;
                let cursor = &cursor;
                let cancel = &cancel;
                scope.spawn(move || {
                    while !cancel.load(Ordering::Relaxed) {
                        let i = cursor.fetch_add(1, Ordering::Relaxed);
                        let Some(&key) = work.get(i) else { break };
                        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            derive_cell(world, parameters, key)
                        }))
                        .unwrap_or_else(|_| Err("Exposed cell worker panicked".into()));
                        let failed = result.is_err();
                        if tx.send(result).is_err() {
                            break;
                        }
                        if failed {
                            cancel.store(true, Ordering::Relaxed);
                            break;
                        }
                    }
                });
            }
            drop(tx);
            for result in rx {
                let outcome = result.and_then(|d| publish(d.record, d.segments, Some(d.bytes)));
                if let Err(error) = outcome {
                    cancel.store(true, Ordering::Relaxed);
                    return Err(error);
                }
            }
            Ok(())
        })?;
    }
    drop(publish);
    if records.len() != total {
        return Err("Incomplete exposed cell worker results".into());
    }
    let mut ordered: Vec<_> = records.into_iter().zip(cells).collect();
    ordered.sort_unstable_by_key(|(record, _)| record.key);
    let (records, cells) = ordered.into_iter().unzip();
    let manifest = Manifest {
        magic: "DWE1".into(),
        schema: 1,
        complete: true,
        base_fingerprint: world.source_fingerprint.clone(),
        scene_fingerprint: world.scene_fingerprint.clone(),
        algorithm_sha256: algorithm(parameters),
        parameters: parameters.clone(),
        cells: records,
        segments: segment_count,
    };
    let bytes = serde_json::to_vec(&manifest).map_err(|e| e.to_string())?;
    atomic(&path, &bytes)?;
    if let Some(journal) = journal {
        journal.finish();
    }
    let stats = json!({"cache_hit":false,"extraction_calls":rebuilt,"rebuilt_cells":rebuilt,"reused_cells":reused,"repaired_payloads":repaired_payloads,"derivation_workers":workers,"elapsed_ms":started.elapsed().as_millis()});
    Ok(Snapshot {
        manifest,
        path,
        manifest_sha256: digest(&bytes),
        cells,
        stats,
    })
}
impl Snapshot {
    pub fn status(&self, rails: usize) -> Value {
        json!({"schema":1,"ready":true,"manifest_file":self.path,"manifest_sha256":self.manifest_sha256,
        "base_fingerprint":self.manifest.base_fingerprint,"scene_fingerprint":self.manifest.scene_fingerprint,"algorithm_sha256":self.manifest.algorithm_sha256,
        "cells":self.manifest.cells.len(),"segments":self.manifest.segments,"rails":rails,"stats":self.stats})
    }
    pub fn checkpoint(&self) -> Option<Value> {
        self.manifest.scene_fingerprint.as_ref().map(|scene|json!({"schema":1,"durable":true,"format":"exposed_edges_v1","scene_fingerprint":scene,
        "manifest_file":self.path,"manifest_sha256":self.manifest_sha256,"algorithm_sha256":self.manifest.algorithm_sha256}))
    }
}

/// Deterministic global stitching restores a shared spline owner at cell seams.
/// Topology is derived in UE f64. Endpoints join only when their final native
/// f32 metre coordinates are identical. Distinct transformed f64 evaluations
/// of that same physics point need no invented gap or movement to share owner.
pub fn rails(snapshot: &Snapshot) -> Result<(Vec<Vec<[f32; 3]>>, Value), String> {
    type Point = [u32; 3];
    let point = |p: [f64; 3]| {
        [p[0] * 0.01, p[2] * 0.01, -p[1] * 0.01].map(|v| {
            let v = v as f32;
            if v == 0. { 0 } else { v.to_bits() }
        })
    };
    let mut seen = HashSet::new();
    let mut edges = Vec::<Segment>::new();
    for cell in &snapshot.cells {
        for segment in cell.iter() {
            let a = point(segment.a);
            let b = point(segment.b);
            let key = if a < b { (a, b) } else { (b, a) };
            if a != b && seen.insert(key) {
                edges.push(if a < b {
                    *segment
                } else {
                    Segment {
                        a: segment.b,
                        b: segment.a,
                    }
                });
            }
        }
    }
    edges.sort_by_key(|s| (point(s.a), point(s.b)));
    drop(seen);
    let mut nodes = HashMap::<Point, Vec<(usize, bool)>>::new();
    for (i, s) in edges.iter().enumerate() {
        nodes.entry(point(s.a)).or_default().push((i, false));
        nodes.entry(point(s.b)).or_default().push((i, true));
    }
    // The user-selected minimum applies to a whole endpoint-connected network,
    // before angle/branch splitting into spline owners. A tiny curved segment,
    // corner or branch remains when attached to at least 50 cm of total edge.
    // Measure the exact submitted f32 points, just as connectivity is defined.
    // Never join a positive gap or remove a short interior span independently.
    let mut visited = vec![false; edges.len()];
    let mut used = vec![false; edges.len()];
    let mut component = Vec::new();
    let mut retained_components = 0usize;
    let mut excluded_components = 0usize;
    let mut excluded_segments = 0usize;
    let mut excluded_length_m = 0f64;
    for seed in 0..edges.len() {
        if visited[seed] {
            continue;
        }
        component.clear();
        component.push(seed);
        visited[seed] = true;
        let mut cursor = 0;
        let mut length = 0f64;
        while cursor < component.len() {
            let edge = &edges[component[cursor]];
            let a = point(edge.a);
            let b = point(edge.b);
            length += (0..3)
                .map(|i| {
                    let d = f64::from(f32::from_bits(b[i])) - f64::from(f32::from_bits(a[i]));
                    d * d
                })
                .sum::<f64>()
                .sqrt();
            for at in [a, b] {
                for &(next, _) in &nodes[&at] {
                    if !visited[next] {
                        visited[next] = true;
                        component.push(next);
                    }
                }
            }
            cursor += 1;
        }
        if length < 0.5 {
            excluded_components += 1;
            excluded_segments += component.len();
            excluded_length_m += length;
            for &edge in &component {
                used[edge] = true;
            }
        } else {
            retained_components += 1;
        }
    }
    drop(visited);
    drop(component);
    let mut result = Vec::new();
    let mut joints = 0usize;
    let unit = |a: [f64; 3], b: [f64; 3]| {
        let a = point(a).map(|v| f64::from(f32::from_bits(v)));
        let b = point(b).map(|v| f64::from(f32::from_bits(v)));
        let d: [f64; 3] = std::array::from_fn(|i| b[i] - a[i]);
        let n = d.iter().map(|v| v * v).sum::<f64>().sqrt();
        d.map(|v| v / n)
    };
    for i in 0..edges.len() {
        if used[i] {
            continue;
        }
        used[i] = true;
        let first = &edges[i];
        let mut line = std::collections::VecDeque::from([first.a, first.b]);
        for backwards in [false, true] {
            let mut at = if backwards { first.a } else { first.b };
            let mut heading = if backwards {
                unit(first.b, first.a)
            } else {
                unit(first.a, first.b)
            };
            loop {
                let around = &nodes[&point(at)];
                if around.len() != 2 {
                    break;
                }
                let Some(&(next, end)) = around.iter().find(|(n, _)| !used[*n]) else {
                    break;
                };
                let edge = &edges[next];
                let to = if end { edge.a } else { edge.b };
                let d = unit(at, to);
                if (0..3).map(|n| heading[n] * d[n]).sum::<f64>() < 0.82 {
                    break;
                }
                used[next] = true;
                joints += 1;
                if backwards {
                    line.push_front(to);
                } else {
                    line.push_back(to);
                }
                at = to;
                heading = d;
            }
        }
        let converted: Vec<[f32; 3]> = line
            .into_iter()
            .map(|p| {
                [
                    (p[0] * 0.01) as f32,
                    (p[2] * 0.01) as f32,
                    (-p[1] * 0.01) as f32,
                ]
            })
            .collect();
        // Conversion can collapse sub-ULP endpoints at distant coordinates.
        // Removing only identical adjacent points cannot create a longer chord.
        let mut rail = Vec::new();
        for p in converted {
            if p.iter().any(|v| !v.is_finite()) {
                return Err("Exposed rail conversion overflow".into());
            }
            if rail.last() != Some(&p) {
                rail.push(p);
            }
        }
        if rail.len() > 1 {
            result.push(rail);
        }
    }
    let count = result.iter().map(|r| r.len() - 1).sum::<usize>();
    Ok((
        result,
        json!({"unique_segments":edges.len(),"duplicate_segments":snapshot.manifest.segments-edges.len(),"stitched_joints":joints,"provider_segments":count,
            "minimum_connected_component_cm":50,"retained_components":retained_components,
            "excluded_components":excluded_components,"excluded_segments":excluded_segments,
            "excluded_arclength_cm":excluded_length_m*100.}),
    ))
}
