//! Fast live deltas from a verified resident snapshot. The extraction kernel,
//! DWE/DWC format and algorithm identity remain the immutable original module.
//! Windows read handles prevent retained content-addressed payloads from being
//! modified/deleted until the final resident reference is released.
use crate::{compact_world::{Bounds, CompactWorld}, exposed_edges::{self,Config,Segment},
    exposed_edge_cache::{self,Parameters,Region,Cell,Manifest,Snapshot}};
use serde_json::{Value,json};
use sha2::{Digest,Sha256};
use std::{collections::HashMap,fs::{self,File,OpenOptions},io::{Read,Write},
    path::{Path,PathBuf},sync::Arc,time::Instant};
const CELL:f64=3200.; const HALO:f64=100.; const MAX_SEGMENTS:usize=50_000_000;
#[path = "change_bounds_index.rs"]
mod change_bounds_index;
type Key=[i32;3]; type Triangle=[[f64;3];3];
trait RegionBounds { fn bound(&self)->Bounds; }
impl RegionBounds for Region {fn bound(&self)->Bounds {Bounds{min:self.min,max:self.max}}}
trait ExtractionConfig {fn config(&self)->Config;}
impl ExtractionConfig for Parameters {fn config(&self)->Config{Config{
    truck_distance_cm:self.truck_distance_cm,tolerance_cm:self.tolerance_cm}}}
use exposed_edge_cache::{algorithm, occupied};
const MAX_CELLS:usize=2_000_000;
struct DerivedCell {record:Cell,segments:Arc<Vec<Segment>>,bytes:Vec<u8>}
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

pub struct Resident {
    identity:String,
    payloads:HashMap<String,Arc<File>>,
}

fn payload_identity(cell:&Cell)->Result<(),String>{
    if !hex(&cell.sha256) || cell.path!=format!("payloads/{}.edges",cell.sha256)
        || cell.segments>MAX_SEGMENTS || cell.bytes!=16+cell.segments as u64*48 {
        return Err("Invalid resident payload identity".into());
    } Ok(())
}
// Callers canonicalize this shared root once, outside the payload loop.
fn lock_read(root:&Path,cell:&Cell)->Result<(Arc<File>,Vec<u8>),String>{
    payload_identity(cell)?;
    let path=root.join(&cell.path);
    if !path.canonicalize().map_err(|e|e.to_string())?.starts_with(root)
        || !fs::symlink_metadata(&path).map_err(|e|e.to_string())?.file_type().is_file(){
        return Err("Resident payload escaped its verified root".into());
    }
    let mut options=OpenOptions::new(); options.read(true);
    #[cfg(windows)] {
        use std::os::windows::fs::OpenOptionsExt;
        options.share_mode(1); // FILE_SHARE_READ; deny writes and deletion.
    }
    let mut file=options.open(&path).map_err(|e|e.to_string())?;
    if file.metadata().map_err(|e|e.to_string())?.len()!=cell.bytes{return Err("Resident payload size changed".into());}
    let mut bytes=Vec::with_capacity(cell.bytes as usize);
    (&mut file).take(cell.bytes+1).read_to_end(&mut bytes).map_err(|e|e.to_string())?;
    if digest(&bytes)!=cell.sha256{return Err("Resident payload content changed".into());}
    Ok((Arc::new(file),bytes))
}
fn lock_payload(root:&Path,cell:&Cell)->Result<Arc<File>,String>{lock_read(root,cell).map(|v|v.0)}
impl Resident {
    /// Validate, decode and lock each unique immutable payload in one pass.
    /// Keeps the original cold reader's manifest and complete occupancy gates.
    pub fn load(base:&Path,world:&CompactWorld,parameters:&Parameters)->Result<(Snapshot,Self),String>{
        if !cfg!(windows) {return Err("Resident payload leases require Windows file sharing semantics".into());}
        let started=Instant::now();
        let(path,manifest,identity)=read_manifest(base,world,parameters)?;
        let root=path.parent().ok_or("Missing resident root")?.canonicalize().map_err(|e|e.to_string())?;
        let mut decoded=HashMap::<(String,u64,usize),Arc<Vec<Segment>>>::new();
        let mut payloads=HashMap::new();
        let mut cells=Vec::with_capacity(manifest.cells.len());
        let total=manifest.cells.len();let mut reported=Instant::now();let mut segment_count=0usize;
        eprintln!("WORLD_EXPOSED_PROGRESS kind=load total={total} cells=0 rebuilt=0 reused=0 segments=0 elapsed_ms=0");
        for cell in &manifest.cells {
            payload_identity(cell)?;
            let key=(cell.sha256.clone(),cell.bytes,cell.segments);
            let segments=if let Some(value)=decoded.get(&key){value.clone()}else{
                let(file,bytes)=lock_read(&root,cell)?;
                let segments=Arc::new(decode(&bytes,cell.segments)?);
                decoded.insert(key,segments.clone());payloads.insert(cell.sha256.clone(),file);segments
            };
            cells.push(segments);
            segment_count+=cell.segments;
            if cells.len()==total || reported.elapsed().as_secs()>=1 {
                eprintln!("WORLD_EXPOSED_PROGRESS kind=load total={total} cells={} rebuilt=0 reused={} segments={segment_count} elapsed_ms={}",
                    cells.len(),cells.len(),started.elapsed().as_millis());
                reported=Instant::now();
            }
        }
        let stats=json!({"cache_hit":true,"extraction_calls":0,"rebuilt_cells":0,
            "reused_cells":manifest.cells.len(),"resident_payload_reuse":true,
            "elapsed_ms":started.elapsed().as_millis()});
        let resident=Self{identity:identity.clone(),payloads};
        Ok((Snapshot{manifest,path,manifest_sha256:identity,cells,stats},resident))
    }

    pub fn new(snapshot:&Snapshot)->Result<Self,String>{
        if !cfg!(windows) {return Err("Resident payload leases require Windows file sharing semantics".into());}
        let root=snapshot.path.parent().ok_or("Missing resident root")?.canonicalize().map_err(|e|e.to_string())?;
        let mut payloads=HashMap::new();
        for cell in &snapshot.manifest.cells {
            payload_identity(cell)?;
            if !payloads.contains_key(&cell.sha256){payloads.insert(cell.sha256.clone(),lock_payload(&root,cell)?);}
        }
        Ok(Self{identity:snapshot.manifest_sha256.clone(),payloads})
    }
    pub fn prepare(&self,base:&Path,world:&CompactWorld,parameters:&Parameters,
        previous:&Snapshot,changes:&[Bounds])->Result<(Snapshot,Self),String>{
        let started=Instant::now();
        if previous.manifest_sha256!=self.identity
            || previous.manifest.base_fingerprint!=world.source_fingerprint
            || previous.manifest.algorithm_sha256!=algorithm(parameters)
            || previous.manifest.parameters!=*parameters
            || previous.cells.len()!=previous.manifest.cells.len()
            || changes.iter().any(|&b|!finite(b)){
            return Err("Resident scene identity or difference mismatch".into());
        }
        let path=location(base,world,parameters)?;
        if path.parent()!=previous.path.parent(){return Err("Resident delta changed payload roots".into());}
        let root=path.parent().ok_or("Missing resident delta root")?;
        let canonical_root=root.canonicalize().map_err(|e|e.to_string())?;
        // Keep the original complete-world occupancy predicate. It is also
        // independently checked by the unmodified cold reader after restart.
        let keys=exposed_edge_cache::occupied(world)?;
        let total=keys.len();let mut reported=Instant::now();
        eprintln!("WORLD_EXPOSED_PROGRESS total={total} cells=0 rebuilt=0 reused=0 segments=0 elapsed_ms=0");
        let old:HashMap<_,_>=previous.manifest.cells.iter().enumerate().map(|(i,c)|(c.key,i)).collect();
        let change_bounds=change_bounds_index::ChangeBoundsIndex::new(changes)?;
        let mut records=Vec::with_capacity(keys.len());let mut cells=Vec::with_capacity(keys.len());
        let mut payloads=HashMap::new();let mut rebuilt=0usize;let mut reused=0usize;let mut count=0usize;
        for key in keys {
            let retained=old.get(&key).and_then(|&i|{
                let cell=&previous.manifest.cells[i];
                (!cell.dependencies.iter().any(|r|change_bounds.any_overlap(r.bound())))
                    .then(||(cell.clone(),previous.cells[i].clone()))
            });
            let (record,segments)=if let Some(value)=retained {reused+=1;value}else{
                let derived=derive_cell(world,parameters,key)?;
                let payload=root.join(&derived.record.path);
                if !self.payloads.contains_key(&derived.record.sha256)
                    && fs::read(&payload).ok().is_none_or(|v|v!=derived.bytes){atomic(&payload,&derived.bytes)?;}
                rebuilt+=1;(derived.record,derived.segments)
            };
            count=count.checked_add(record.segments).ok_or("Resident segment count overflow")?;
            if count>MAX_SEGMENTS{return Err("Resident segment budget exceeded".into());}
            if !payloads.contains_key(&record.sha256){
                let lease=match self.payloads.get(&record.sha256){Some(file)=>file.clone(),None=>lock_payload(&canonical_root,&record)?};
                payloads.insert(record.sha256.clone(),lease);
            }
            records.push(record);cells.push(segments);
            if cells.len()==total || reported.elapsed().as_secs()>=1 {
                eprintln!("WORLD_EXPOSED_PROGRESS total={total} cells={} rebuilt={rebuilt} reused={reused} segments={count} elapsed_ms={}",
                    cells.len(),started.elapsed().as_millis());
                reported=Instant::now();
            }
        }
        let manifest=Manifest{magic:"DWE1".into(),schema:1,complete:true,
            base_fingerprint:world.source_fingerprint.clone(),scene_fingerprint:world.scene_fingerprint.clone(),
            algorithm_sha256:algorithm(parameters),parameters:parameters.clone(),cells:records,segments:count};
        let bytes=serde_json::to_vec(&manifest).map_err(|e|e.to_string())?;
        atomic(&path,&bytes)?;let identity=digest(&bytes);
        let stats=json!({"cache_hit":false,"extraction_calls":rebuilt,"rebuilt_cells":rebuilt,
            "reused_cells":reused,"resident_payload_reuse":true,"derivation_workers":1,
            "elapsed_ms":started.elapsed().as_millis()});
        eprintln!("WORLD_RESIDENT_DELTA cells={} rebuilt={rebuilt} reused={reused} elapsed_ms={}",
            manifest.cells.len(),started.elapsed().as_millis());
        Ok((Snapshot{manifest,path,manifest_sha256:identity.clone(),cells,stats},Self{identity,payloads}))
    }
}
