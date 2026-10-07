//! Incremental native endpoint graph. Extraction identities belong to DWE;
//! this independently versioned index owns connectivity, the global 50 cm
//! component test, native spline stitching, and stable provider owners.
use crate::exposed_edges::Segment;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BinaryHeap, HashMap, HashSet, VecDeque};
use std::ops::{Deref, DerefMut};
use std::sync::Arc;
use std::{
    fs,
    io::{BufReader, BufWriter, Read, Seek, SeekFrom, Write},
    path::Path,
};

pub type CellKey = [i32; 3];
type Point = [u32; 3];
type EdgeKey = [Point; 2];
const UNASSIGNED: u32 = u32::MAX;
const LIMIT: usize = 50_000_000;
pub const VERSION: u32 = 1;
/// This pin changes independently of extraction/DWC identities.
pub fn algorithm(extraction_pin: &str) -> String {
    let mut hash = Sha256::new();
    hash.update(b"Dragonwild incremental grind graph v1\0");
    hash.update(extraction_pin.as_bytes());
    hash.update(include_bytes!("grind_path_index.rs"));
    hash.update(include_bytes!("../Cargo.lock"));
    hash.finalize().iter().map(|v| format!("{v:02x}")).collect()
}

pub struct CellInput<'a> {
    pub key: CellKey,
    /// Identity of an already verified DWC payload, not a geometry name.
    pub identity: &'a str,
    pub segments: &'a [Segment],
}

const CACHE_HEADER: usize = 128;
fn pin(s: &str) -> Result<[u8; 32], String> {
    if s.len() != 64
        || !s
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err("Invalid grind graph cache identity".into());
    }
    Ok(std::array::from_fn(|i| {
        u8::from_str_radix(&s[i * 2..i * 2 + 2], 16).unwrap()
    }))
}
struct CacheWriter<W: Write> {
    inner: W,
    hash: Sha256,
    bytes: u64,
}
impl<W: Write> CacheWriter<W> {
    fn bytes(&mut self, b: &[u8]) -> Result<(), String> {
        self.inner.write_all(b).map_err(|e| e.to_string())?;
        self.hash.update(b);
        self.bytes += b.len() as u64;
        Ok(())
    }
    fn count(&mut self, n: usize) -> Result<(), String> {
        self.bytes(&(n as u64).to_le_bytes())
    }
    fn key(&mut self, key: EdgeKey) -> Result<(), String> {
        for value in key.into_iter().flatten() {
            self.bytes(&value.to_le_bytes())?;
        }
        Ok(())
    }
}
struct CacheReader<R: Read> {
    inner: R,
    remaining: u64,
}
impl<R: Read> CacheReader<R> {
    fn bytes<const N: usize>(&mut self) -> Result<[u8; N], String> {
        if N as u64 > self.remaining {
            return Err("Truncated grind graph cache".into());
        }
        let mut b = [0; N];
        self.inner.read_exact(&mut b).map_err(|e| e.to_string())?;
        self.remaining -= N as u64;
        Ok(b)
    }
    fn u32(&mut self) -> Result<u32, String> {
        Ok(u32::from_le_bytes(self.bytes()?))
    }
    fn u64(&mut self) -> Result<u64, String> {
        Ok(u64::from_le_bytes(self.bytes()?))
    }
    fn count(&mut self, max: usize, unit: u64) -> Result<usize, String> {
        let n = self.u64()?;
        if n > max as u64 || n.checked_mul(unit).is_none_or(|n| n > self.remaining) {
            return Err("Grind graph cache count limit".into());
        }
        Ok(n as usize)
    }
    fn key(&mut self) -> Result<EdgeKey, String> {
        let key = [
            [self.u32()?, self.u32()?, self.u32()?],
            [self.u32()?, self.u32()?, self.u32()?],
        ];
        if key[0] >= key[1]
            || key
                .into_iter()
                .flatten()
                .any(|v| !f32::from_bits(v).is_finite() || v == 0x80000000)
        {
            return Err("Invalid cached grind edge".into());
        }
        Ok(key)
    }
}

impl GrindPathIndex {
    /// A separate DGI1 payload, bound to the exact DWE manifest and algorithm.
    /// Loading restores topology and prebuilt owners; it never restitches rails.
    pub fn store(&self, path: &Path, algorithm: &str) -> Result<(), String> {
        if self.pending.is_some() {
            return Err("Cannot persist pending grind graph".into());
        }
        let mut header = [0; CACHE_HEADER];
        header[..4].copy_from_slice(b"DGI1");
        header[4..8].copy_from_slice(&VERSION.to_le_bytes());
        header[8..40].copy_from_slice(&pin(algorithm)?);
        header[40..72].copy_from_slice(&pin(&self.identity)?);
        let parent = path.parent().ok_or("Missing grind graph parent")?;
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        let parent = parent.canonicalize().map_err(|e| e.to_string())?;
        use std::sync::atomic::{AtomicU64, Ordering};
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let temporary = path.with_extension(format!(
            "{}-{}.tmp",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let result = (|| {
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temporary)
                .map_err(|e| e.to_string())?;
            file.write_all(&header).map_err(|e| e.to_string())?;
            let (hash, bytes) = {
                let mut out = CacheWriter {
                    inner: BufWriter::with_capacity(256 * 1024, &mut file),
                    hash: Sha256::new(),
                    bytes: 0,
                };
                for n in [
                    self.raw_count,
                    self.cells.len(),
                    self.edges.len(),
                    self.components.len(),
                    self.rails.len(),
                ] {
                    out.count(n)?;
                }
                out.bytes(&self.next_owner.to_le_bytes())?;
                for n in [
                    self.census.retained,
                    self.census.excluded,
                    self.census.excluded_segments,
                    self.census.joints,
                    self.census.provider_segments,
                ] {
                    out.count(n)?;
                }
                out.bytes(&self.census.excluded_length.to_le_bytes())?;
                let mut cells: Vec<_> = self.cells.iter().collect();
                cells.sort_unstable_by_key(|(key, _)| **key);
                for (key, cell) in cells {
                    for v in key {
                        out.bytes(&v.to_le_bytes())?;
                    }
                    out.count(cell.identity.len())?;
                    out.bytes(cell.identity.as_bytes())?;
                    out.count(cell.raw_count)?;
                    out.count(cell.edges.len())?;
                    for id in &cell.edges {
                        out.bytes(&id.to_le_bytes())?;
                    }
                }
                for edge in &self.edges {
                    out.bytes(&[u8::from(edge.is_some())])?;
                    if let Some(edge) = edge {
                        out.key(edge.key)?;
                        out.bytes(&edge.refs.to_le_bytes())?;
                        out.bytes(&edge.component.to_le_bytes())?;
                        out.bytes(&[edge.zero_signs])?;
                    }
                }
                let mut components: Vec<_> = self.components.iter().collect();
                components.sort_unstable_by_key(|(id, _)| **id);
                for (id, component) in components {
                    out.bytes(&id.to_le_bytes())?;
                    out.bytes(&component.length.to_le_bytes())?;
                    out.count(component.joints)?;
                    out.count(component.edges.len())?;
                    for id in component.edges.iter() {
                        out.bytes(&id.to_le_bytes())?;
                    }
                    out.count(component.seeds.len())?;
                    for &seed in &component.seeds {
                        out.key(self.edge(seed).key)?;
                    }
                }
                for (&seed, rail) in &self.rails {
                    out.key(seed)?;
                    out.bytes(&rail.owner.to_le_bytes())?;
                    out.count(rail.points.len())?;
                    for p in rail.points.iter() {
                        for v in p {
                            out.bytes(&v.to_le_bytes())?;
                        }
                    }
                }
                out.inner.flush().map_err(|e| e.to_string())?;
                (out.hash.finalize(), out.bytes)
            };
            header[72..104].copy_from_slice(&hash);
            header[104..112].copy_from_slice(&bytes.to_le_bytes());
            file.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
            file.write_all(&header).map_err(|e| e.to_string())?;
            file.sync_all().map_err(|e| e.to_string())?;
            drop(file);
            if path
                .parent()
                .unwrap()
                .canonicalize()
                .map_err(|e| e.to_string())?
                != parent
            {
                return Err("Grind graph parent changed".into());
            }
            // Graph payloads are immutable. Replacing an old scene identity
            // could replace an ancestor of this process's live delta chain.
            fs::hard_link(&temporary, path).map_err(|e| e.to_string())?;
            fs::remove_file(&temporary).map_err(|e| e.to_string())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        result
    }
    /// Compact scene updates reference a complete base or another delta. The
    /// dirty-cell payload, parent identity and checksum are verified on replay.
    /// This avoids serializing the entire graph for a small gameplay edit.
    pub fn store_delta(
        &self,
        path: &Path,
        parent: &Path,
        transaction: &Transaction,
        algorithm: &str,
    ) -> Result<(), String> {
        if self.pending != Some(transaction.serial) {
            return Err("Stale persisted grind transaction".into());
        }
        if self.delta_depth > 256 {
            return Err("Grind graph delta chain needs compaction".into());
        }
        let folder = path
            .parent()
            .ok_or("Missing graph delta parent")?
            .canonicalize()
            .map_err(|e| e.to_string())?;
        if parent
            .parent()
            .ok_or("Missing graph ancestor parent")?
            .canonicalize()
            .map_err(|e| e.to_string())?
            != folder
        {
            return Err("Graph delta ancestor outside cache".into());
        }
        let name = parent
            .file_name()
            .and_then(|v| v.to_str())
            .ok_or("Invalid graph ancestor name")?;
        if name.len() > 256 {
            return Err("Oversized graph ancestor name".into());
        }
        let mut header = [0; CACHE_HEADER];
        header[..4].copy_from_slice(b"DGD1");
        header[4..8].copy_from_slice(&VERSION.to_le_bytes());
        header[8..40].copy_from_slice(&pin(algorithm)?);
        header[40..72].copy_from_slice(&pin(&self.identity)?);
        let temporary = path.with_extension(format!(
            "{}-{}.delta.tmp",
            std::process::id(),
            transaction.serial
        ));
        let result = (|| {
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temporary)
                .map_err(|e| e.to_string())?;
            file.write_all(&header).map_err(|e| e.to_string())?;
            let (hash, bytes) = {
                let mut out = CacheWriter {
                    inner: BufWriter::new(&mut file),
                    hash: Sha256::new(),
                    bytes: 0,
                };
                out.bytes(&pin(&transaction.previous_identity)?)?;
                out.count(name.len())?;
                out.bytes(name.as_bytes())?;
                out.bytes(&[u8::from(transaction.owner_rebound)])?;
                out.count(transaction.replacements.len())?;
                for (key, cell) in &transaction.replacements {
                    for v in key {
                        out.bytes(&v.to_le_bytes())?;
                    }
                    out.bytes(&[u8::from(cell.is_some())])?;
                    if let Some(cell) = cell {
                        out.count(cell.identity.len())?;
                        out.bytes(cell.identity.as_bytes())?;
                        out.count(cell.raw_count)?;
                        out.count(cell.edges.len())?;
                        for (&edge, &signs) in cell.edges.iter().zip(&cell.zero_signs) {
                            out.key(edge)?;
                            out.bytes(&[signs])?;
                        }
                    }
                }
                out.inner.flush().map_err(|e| e.to_string())?;
                (out.hash.finalize(), out.bytes)
            };
            header[72..104].copy_from_slice(&hash);
            header[104..112].copy_from_slice(&bytes.to_le_bytes());
            file.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
            file.write_all(&header).map_err(|e| e.to_string())?;
            file.sync_all().map_err(|e| e.to_string())?;
            drop(file);
            if path
                .parent()
                .unwrap()
                .canonicalize()
                .map_err(|e| e.to_string())?
                != folder
            {
                return Err("Graph delta parent changed".into());
            }
            fs::hard_link(&temporary, path).map_err(|e| e.to_string())?;
            fs::remove_file(&temporary).map_err(|e| e.to_string())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        result
    }
    /// Bind a restored topology to a fresh native Session whose initial owners
    /// are the current globally sorted rail ordinals, irrespective of old runs.
    pub fn rebind_initial_owners(&mut self) -> Result<(), String> {
        if self.pending.is_some() {
            return Err("Cannot rebind pending grind owners".into());
        }
        self.next_owner = 1;
        for rail in self.rails.values_mut() {
            rail.owner = self.next_owner;
            self.next_owner += 1;
        }
        self.owner_rebound = true;
        Ok(())
    }
    pub fn delta_depth(&self) -> usize {
        self.delta_depth
    }
    /// Infrequent background compaction writes the already prepared candidate
    /// graph, without connectivity, stitching, or provider reconstruction.
    pub fn store_proposed(
        &mut self,
        path: &Path,
        algorithm: &str,
        transaction: &Transaction,
    ) -> Result<(), String> {
        if self.pending != Some(transaction.serial)
            || self.identity != transaction.proposed_identity
        {
            return Err("Stale proposed grind graph publication".into());
        }
        // Exclusive mutable access prevents any other graph operation during
        // this synchronous write. Restore the transaction guard on every path.
        let pending = self.pending.take();
        let result = self.store(path, algorithm);
        self.pending = pending;
        if result.is_ok() {
            self.delta_depth = 0;
        }
        result
    }
    pub fn load(path: &Path, expected: &str, algorithm: &str) -> Result<Self, String> {
        Self::load_depth(path, expected, algorithm, 0)
    }
    fn load_depth(
        path: &Path,
        expected: &str,
        algorithm: &str,
        depth: usize,
    ) -> Result<Self, String> {
        if depth > 256 {
            return Err("Grind graph delta chain needs offline compaction".into());
        }
        if !fs::symlink_metadata(path)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_file()
        {
            return Err("Grind graph cache is not regular".into());
        }
        let file = fs::File::open(path).map_err(|e| e.to_string())?;
        let size = file.metadata().map_err(|e| e.to_string())?.len();
        let mut input = BufReader::with_capacity(256 * 1024, file);
        let mut header = [0; CACHE_HEADER];
        input.read_exact(&mut header).map_err(|e| e.to_string())?;
        let bytes = u64::from_le_bytes(header[104..112].try_into().unwrap());
        if (&header[..4] != b"DGI1" && &header[..4] != b"DGD1")
            || header[4..8] != VERSION.to_le_bytes()
            || header[8..40] != pin(algorithm)?
            || header[40..72] != pin(expected)?
            || header[112..].iter().any(|&b| b != 0)
            || bytes.checked_add(CACHE_HEADER as u64) != Some(size)
            || size > 16 * 1024 * 1024 * 1024
        {
            return Err("Grind graph cache version, identity or size mismatch".into());
        }
        // Authenticate before parsing allocation counts or graph structure.
        let mut hash = Sha256::new();
        // A journal can have up to 256 ancestors. Keep the checksum scratch
        // off the recursive stack and release it before reading an ancestor.
        let mut buffer = vec![0; 65536];
        loop {
            let n = input.read(&mut buffer).map_err(|e| e.to_string())?;
            if n == 0 {
                break;
            }
            hash.update(&buffer[..n]);
        }
        if hash.finalize().as_slice() != &header[72..104] {
            return Err("Grind graph cache checksum mismatch".into());
        }
        drop(buffer);
        input
            .seek(SeekFrom::Start(CACHE_HEADER as u64))
            .map_err(|e| e.to_string())?;
        let mut input = CacheReader {
            inner: input,
            remaining: bytes,
        };
        if &header[..4] == b"DGD1" {
            let previous = input
                .bytes::<32>()?
                .iter()
                .map(|v| format!("{v:02x}"))
                .collect::<String>();
            let n = input.count(256, 1)?;
            let mut name = vec![0; n];
            input
                .inner
                .read_exact(&mut name)
                .map_err(|e| e.to_string())?;
            input.remaining -= n as u64;
            let name = String::from_utf8(name).map_err(|e| e.to_string())?;
            let mut components = Path::new(&name).components();
            if !matches!(components.next(), Some(std::path::Component::Normal(_)))
                || components.next().is_some()
            {
                return Err("Invalid graph delta ancestor path".into());
            }
            let rebind = match input.bytes::<1>()?[0] {
                0 => false,
                1 => true,
                _ => return Err("Invalid graph owner rebind flag".into()),
            };
            let n = input.count(2_000_000, 13)?;
            let mut replacements = Vec::with_capacity(n);
            let mut prior_key = None;
            let mut total_edges = 0usize;
            for _ in 0..n {
                let key = [
                    i32::from_le_bytes(input.bytes()?),
                    i32::from_le_bytes(input.bytes()?),
                    i32::from_le_bytes(input.bytes()?),
                ];
                if prior_key.is_some_and(|p| p >= key) {
                    return Err("Unordered graph delta cells".into());
                }
                prior_key = Some(key);
                let cell = match input.bytes::<1>()?[0] {
                    0 => None,
                    1 => {
                        let n = input.count(512, 1)?;
                        let mut identity = vec![0; n];
                        input
                            .inner
                            .read_exact(&mut identity)
                            .map_err(|e| e.to_string())?;
                        input.remaining -= n as u64;
                        let identity = String::from_utf8(identity).map_err(|e| e.to_string())?;
                        let raw_count = input.count(LIMIT, 0)?;
                        let n = input.count(raw_count, 25)?;
                        total_edges += n;
                        if total_edges > LIMIT {
                            return Err("Graph delta segment limit".into());
                        }
                        let mut edges = Vec::with_capacity(n);
                        let mut zero_signs = Vec::with_capacity(n);
                        for _ in 0..n {
                            let edge = input.key()?;
                            let signs = input.bytes::<1>()?[0];
                            if signs >= 64 || edges.last().is_some_and(|&prior| prior >= edge) {
                                return Err("Invalid graph delta edge".into());
                            }
                            edges.push(edge);
                            zero_signs.push(signs);
                        }
                        Some(StagedCell {
                            identity,
                            raw_count,
                            edges,
                            zero_signs,
                        })
                    }
                    _ => return Err("Invalid graph delta cell flag".into()),
                };
                replacements.push((key, cell));
            }
            if input.remaining != 0 {
                return Err("Trailing graph delta bytes".into());
            }
            drop(input);
            let mut index = Self::load_depth(
                &path
                    .parent()
                    .ok_or("Missing graph delta folder")?
                    .join(name),
                &previous,
                algorithm,
                depth + 1,
            )?;
            if rebind {
                index.rebind_initial_owners()?;
            }
            let mut raw = index.raw_count;
            for (key, cell) in &replacements {
                if let Some(old) = index.cells.get(key) {
                    raw -= old.raw_count;
                }
                if let Some(cell) = cell {
                    raw = raw
                        .checked_add(cell.raw_count)
                        .ok_or("Graph delta raw count overflow")?;
                }
            }
            if raw > LIMIT {
                return Err("Graph delta raw count limit".into());
            }
            let mut stats = UpdateStats::default();
            stats.changed_cells = replacements.len();
            index.replace(replacements, &mut stats);
            index.last_update = stats;
            index.identity = expected.into();
            index.owner_rebound = false;
            index.delta_depth += 1;
            return Ok(index);
        }
        let raw_count = input.count(LIMIT, 0)?;
        let cell_count = input.count(2_000_000, 36)?;
        let slot_count = input.count(LIMIT, 1)?;
        let component_count = input.count(LIMIT, 36)?;
        let rail_count = input.count(LIMIT, 64)?;
        let next_owner = input.u64()?;
        let census = Census {
            retained: input.count(LIMIT, 0)?,
            excluded: input.count(LIMIT, 0)?,
            excluded_segments: input.count(LIMIT, 0)?,
            joints: input.count(LIMIT, 0)?,
            provider_segments: input.count(LIMIT, 0)?,
            excluded_length: f64::from_le_bytes(input.bytes()?),
        };
        if !census.excluded_length.is_finite() || census.excluded_length < -1e-6 {
            return Err("Invalid graph census".into());
        }
        let mut index = Self {
            cells: HashMap::with_capacity(cell_count),
            lookup: HashMap::with_capacity(slot_count),
            edges: Vec::with_capacity(slot_count),
            free: BinaryHeap::new(),
            nodes: HashMap::new(),
            components: HashMap::with_capacity(component_count),
            rails: BTreeMap::new(),
            raw_count,
            next_owner,
            identity: expected.into(),
            serial: 0,
            pending: None,
            last_update: UpdateStats::default(),
            census,
            owner_rebound: false,
            delta_depth: 0,
        };
        let mut raw = 0usize;
        let mut reference_count = 0usize;
        for _ in 0..cell_count {
            let key = [
                i32::from_le_bytes(input.bytes()?),
                i32::from_le_bytes(input.bytes()?),
                i32::from_le_bytes(input.bytes()?),
            ];
            let n = input.count(512, 1)?;
            let mut identity = vec![0; n];
            input
                .inner
                .read_exact(&mut identity)
                .map_err(|e| e.to_string())?;
            input.remaining -= n as u64;
            let identity = String::from_utf8(identity).map_err(|e| e.to_string())?;
            let count = input.count(LIMIT, 0)?;
            raw = raw.checked_add(count).ok_or("Graph count overflow")?;
            let n = input.count(count, 4)?;
            reference_count = reference_count
                .checked_add(n)
                .ok_or("Graph reference overflow")?;
            if reference_count > LIMIT {
                return Err("Graph reference limit".into());
            }
            let mut ids = Vec::with_capacity(n);
            let mut unique = HashSet::with_capacity(n);
            for _ in 0..n {
                let id = input.u32()?;
                if id as usize >= slot_count || !unique.insert(id) {
                    return Err("Invalid graph cell edge".into());
                }
                ids.push(id);
            }
            if index
                .cells
                .insert(
                    key,
                    Cell {
                        identity,
                        raw_count: count,
                        edges: ids,
                    },
                )
                .is_some()
            {
                return Err("Duplicate cached graph cell".into());
            }
        }
        if raw != raw_count {
            return Err("Graph raw count mismatch".into());
        }
        for id in 0..slot_count {
            match input.bytes::<1>()?[0] {
                0 => {
                    index.edges.push(None);
                    index.free.push(id as u32);
                }
                1 => {
                    let key = input.key()?;
                    let refs = input.u32()?;
                    let component = input.u32()?;
                    let zero_signs = input.bytes::<1>()?[0];
                    if refs == 0
                        || component as usize >= slot_count
                        || zero_signs >= 64
                        || index.lookup.insert(key, id as u32).is_some()
                    {
                        return Err("Invalid cached edge identity".into());
                    }
                    index.edges.push(Some(Edge {
                        key,
                        refs,
                        component,
                        zero_signs,
                    }));
                    for at in key {
                        index
                            .nodes
                            .entry(at)
                            .and_modify(|around| around.push(id as u32))
                            .or_insert(Around::One(id as u32));
                    }
                }
                _ => return Err("Invalid cached edge slot".into()),
            }
        }
        let mut refs = vec![0u32; slot_count];
        for cell in index.cells.values() {
            for &id in &cell.edges {
                if index.edges[id as usize].is_none() {
                    return Err("Cell references empty graph edge".into());
                }
                refs[id as usize] += 1;
            }
        }
        for (id, edge) in index.edges.iter().enumerate() {
            if let Some(edge) = edge {
                if edge.refs != refs[id] {
                    return Err("Graph reference census mismatch".into());
                }
            }
        }
        drop(refs);
        let mut covered = vec![false; slot_count];
        let mut rail_seeds = vec![false; slot_count];
        let mut rail_seed_count = 0;
        let mut check = Census::default();
        let mut component_edges = 0usize;
        for _ in 0..component_count {
            let id = input.u32()?;
            let length = f64::from_le_bytes(input.bytes()?);
            let joints = input.count(LIMIT, 0)?;
            let n = input.count(LIMIT, 4)?;
            component_edges = component_edges
                .checked_add(n)
                .ok_or("Graph component overflow")?;
            if n == 0 || !length.is_finite() || length < 0. || component_edges > LIMIT {
                return Err("Invalid cached component".into());
            }
            let mut ids = Vec::with_capacity(n);
            let mut previous = None;
            for _ in 0..n {
                let member = input.u32()?;
                let edge = index
                    .edges
                    .get(member as usize)
                    .and_then(Option::as_ref)
                    .ok_or("Empty cached component edge")?;
                if edge.component != id
                    || covered[member as usize]
                    || previous.is_some_and(|key| key >= edge.key)
                {
                    return Err("Invalid component membership".into());
                }
                covered[member as usize] = true;
                previous = Some(edge.key);
                ids.push(member);
            }
            if ids[0] != id {
                return Err("Invalid component seed".into());
            }
            let n = input.count(ids.len(), 24)?;
            let mut seeds = Vec::with_capacity(n);
            let mut previous_seed = None;
            for _ in 0..n {
                let seed = input.key()?;
                let seed_id = *index
                    .lookup
                    .get(&seed)
                    .ok_or("Missing cached component rail seed")?;
                if rail_seeds[seed_id as usize] || previous_seed.is_some_and(|key| key >= seed) {
                    return Err("Duplicate cached rail seed".into());
                }
                rail_seeds[seed_id as usize] = true;
                rail_seed_count += 1;
                previous_seed = Some(seed);
                seeds.push(seed_id);
            }
            if length >= 0.5 {
                check.retained += 1;
                check.joints += joints;
                if seeds.is_empty() {
                    return Err("Retained component missing rails".into());
                }
            } else {
                check.excluded += 1;
                check.excluded_segments += ids.len();
                if !seeds.is_empty() || joints != 0 {
                    return Err("Short component has rails".into());
                }
            }
            if index
                .components
                .insert(
                    id,
                    Component {
                        edges: ids.into(),
                        seeds: seeds.into_boxed_slice(),
                        length,
                        joints,
                    },
                )
                .is_some()
            {
                return Err("Duplicate cached component".into());
            }
        }
        if component_edges != index.lookup.len() || rail_seed_count != rail_count {
            return Err("Graph membership census mismatch".into());
        }
        let mut owners = HashSet::new();
        let mut points = 0usize;
        let mut used_edges = vec![false; slot_count];
        let mut used_edge_count = 0;
        for _ in 0..rail_count {
            let seed = input.key()?;
            let seed_id = *index.lookup.get(&seed).ok_or("Missing cached rail seed")?;
            let owner = input.u64()?;
            let n = input.count(LIMIT + 1, 12)?;
            points += n;
            if n < 2
                || points > 2 * LIMIT
                || owner == 0
                || owner >= next_owner
                || !owners.insert(owner)
                || !rail_seeds[seed_id as usize]
            {
                return Err("Invalid cached owner or rail length".into());
            }
            rail_seeds[seed_id as usize] = false;
            let component = index.edge(seed_id).component;
            let mut rail = Vec::<[f32; 3]>::with_capacity(n);
            for _ in 0..n {
                let p = [
                    f32::from_le_bytes(input.bytes()?),
                    f32::from_le_bytes(input.bytes()?),
                    f32::from_le_bytes(input.bytes()?),
                ];
                if p.iter().any(|v| !v.is_finite()) || rail.last() == Some(&p) {
                    return Err("Invalid cached rail point".into());
                }
                if let Some(previous) = rail.last() {
                    let a = previous.map(|v| if v == 0. { 0 } else { v.to_bits() });
                    let b = p.map(|v| if v == 0. { 0 } else { v.to_bits() });
                    let key = if a < b { [a, b] } else { [b, a] };
                    let id = *index.lookup.get(&key).ok_or("Cached rail invents edge")?;
                    if index.edge(id).component != component || used_edges[id as usize] {
                        return Err("Cached rail component mismatch".into());
                    }
                    used_edges[id as usize] = true;
                    used_edge_count += 1;
                }
                rail.push(p);
            }
            check.provider_segments += n - 1;
            index.rails.insert(
                seed,
                Rail {
                    owner,
                    points: Arc::new(rail),
                },
            );
        }
        if input.remaining != 0
            || check.retained != census.retained
            || check.excluded != census.excluded
            || check.excluded_segments != census.excluded_segments
            || check.joints != census.joints
            || check.provider_segments != census.provider_segments
            || used_edge_count + check.excluded_segments != index.lookup.len()
        {
            return Err("Cached graph output census mismatch".into());
        }
        for around in index.nodes.values_mut() {
            around.sort_unstable_by_key(|&id| index.edges[id as usize].as_ref().unwrap().key);
        }
        for around in index.nodes.values() {
            let component = index.edge(around[0]).component;
            if around
                .iter()
                .any(|&id| index.edge(id).component != component)
            {
                return Err("Cached graph splits connected endpoints".into());
            }
        }
        Ok(index)
    }
}

#[cfg(test)]
mod storage_tests {
    use super::*;
    #[test]
    #[cfg(target_pointer_width = "64")]
    fn compact_layout_keeps_small_neighborhoods_inline() {
        assert_eq!(std::mem::size_of::<Around>(), 24);
        assert_eq!(std::mem::size_of::<Members>(), 16);
        assert_eq!(std::mem::size_of::<Component>(), 48);
        assert!(matches!(Members::from(vec![7]), Members::One(7)));
    }
    #[test]
    fn neighborhood_mutations_cross_one_two_many_and_empty() {
        let mut around = Around::One(4);
        around.push(2);
        assert!(matches!(around, Around::Two(_)));
        around.push(3);
        around.push(1);
        assert!(matches!(around, Around::Many(_)));
        around.sort_unstable();
        assert_eq!(&*around, &[1, 2, 3, 4]);
        assert!(!around.remove(99));
        assert!(!around.remove(2));
        assert!(!around.remove(4));
        assert!(matches!(around, Around::Two([1, 3])));
        assert!(!around.remove(1));
        assert!(matches!(around, Around::One(3)));
        assert!(!around.remove(99));
        assert!(around.remove(3));
    }
}
#[derive(Clone)]
struct Cell {
    identity: String,
    raw_count: usize,
    edges: Vec<u32>,
}
#[derive(Clone)]
struct StagedCell {
    identity: String,
    raw_count: usize,
    edges: Vec<EdgeKey>,
    zero_signs: Vec<u8>,
}
#[derive(Clone, Copy)]
struct Edge {
    key: EdgeKey,
    refs: u32,
    component: u32,
    zero_signs: u8,
}
struct Component {
    edges: Members,
    seeds: Box<[u32]>,
    length: f64,
    joints: usize,
}
/// Most components and endpoint neighborhoods contain one or two edges.
/// Keep those inline; boxed member lists have no unused vector capacity.
enum Members {
    One(u32),
    Many(Box<[u32]>),
}
impl From<Vec<u32>> for Members {
    fn from(edges: Vec<u32>) -> Self {
        if edges.len() == 1 {
            Self::One(edges[0])
        } else {
            Self::Many(edges.into_boxed_slice())
        }
    }
}
impl Deref for Members {
    type Target = [u32];
    fn deref(&self) -> &[u32] {
        match self {
            Self::One(id) => std::slice::from_ref(id),
            Self::Many(ids) => ids,
        }
    }
}
enum Around {
    One(u32),
    Two([u32; 2]),
    Many(Vec<u32>),
}
impl Around {
    fn push(&mut self, id: u32) {
        match self {
            Self::One(first) => *self = Self::Two([*first, id]),
            Self::Two(pair) => *self = Self::Many(vec![pair[0], pair[1], id]),
            Self::Many(ids) => ids.push(id),
        }
    }
    /// The map removes an empty neighborhood immediately.
    fn remove(&mut self, id: u32) -> bool {
        match self {
            Self::One(first) => *first == id,
            Self::Two(pair) => {
                if pair[0] == id {
                    *self = Self::One(pair[1]);
                } else if pair[1] == id {
                    *self = Self::One(pair[0]);
                }
                false
            }
            Self::Many(ids) => {
                ids.retain(|&next| next != id);
                match ids.len() {
                    0 => return true,
                    1 => *self = Self::One(ids[0]),
                    2 => *self = Self::Two([ids[0], ids[1]]),
                    _ => (),
                }
                false
            }
        }
    }
}
impl Deref for Around {
    type Target = [u32];
    fn deref(&self) -> &[u32] {
        match self {
            Self::One(id) => std::slice::from_ref(id),
            Self::Two(ids) => ids,
            Self::Many(ids) => ids,
        }
    }
}
impl DerefMut for Around {
    fn deref_mut(&mut self) -> &mut [u32] {
        match self {
            Self::One(id) => std::slice::from_mut(id),
            Self::Two(ids) => ids,
            Self::Many(ids) => ids,
        }
    }
}
impl<'a> IntoIterator for &'a Around {
    type Item = &'a u32;
    type IntoIter = std::slice::Iter<'a, u32>;
    fn into_iter(self) -> Self::IntoIter {
        self.iter()
    }
}
#[derive(Clone)]
struct Rail {
    owner: u64,
    points: Arc<Vec<[f32; 3]>>,
}
#[derive(Clone, Copy, Default)]
struct Census {
    retained: usize,
    excluded: usize,
    excluded_segments: usize,
    excluded_length: f64,
    joints: usize,
    provider_segments: usize,
}

#[derive(Debug, Default)]
pub struct GraphDelta {
    pub retired_owners: Vec<u64>,
    pub rails: Vec<(u64, Vec<[f32; 3]>)>,
}
#[derive(Debug, Default, Clone)]
pub struct UpdateStats {
    pub changed_cells: usize,
    pub reused_cells: usize,
    pub input_segments_examined: usize,
    pub topology_edges_added: usize,
    pub topology_edges_removed: usize,
    pub rebuilt_components: usize,
    pub rebuilt_component_edges: usize,
}
/// There can be only one open transaction. The undo log stores dirty cells and
/// affected rail owners, never a clone of the whole world's graph.
pub struct Transaction {
    pub delta: GraphDelta,
    pub stats: UpdateStats,
    serial: u64,
    previous_identity: String,
    undo: Vec<(CellKey, Option<StagedCell>)>,
    old_rails: BTreeMap<EdgeKey, Rail>,
    next_owner: u64,
    census: Census,
    replacements: Vec<(CellKey, Option<StagedCell>)>,
    owner_rebound: bool,
    delta_depth: usize,
    proposed_identity: String,
}
pub struct GrindPathIndex {
    cells: HashMap<CellKey, Cell>,
    lookup: HashMap<EdgeKey, u32>,
    edges: Vec<Option<Edge>>,
    // Highest vacant slot wins, also after a full checkpoint is reloaded.
    free: BinaryHeap<u32>,
    nodes: HashMap<Point, Around>,
    components: HashMap<u32, Component>,
    rails: BTreeMap<EdgeKey, Rail>,
    raw_count: usize,
    next_owner: u64,
    identity: String,
    serial: u64,
    pending: Option<u64>,
    last_update: UpdateStats,
    census: Census,
    owner_rebound: bool,
    delta_depth: usize,
}

fn point(p: [f64; 3]) -> Result<Point, String> {
    let p = [p[0] * 0.01, p[2] * 0.01, -p[1] * 0.01].map(|v| v as f32);
    if p.iter().any(|v| !v.is_finite()) {
        return Err("Exposed rail conversion overflow".into());
    }
    Ok(p.map(|v| if v == 0. { 0 } else { v.to_bits() }))
}
fn native(p: Point) -> [f32; 3] {
    p.map(f32::from_bits)
}
fn zero_signs(segment: &Segment, reverse: bool) -> u8 {
    let raw = |p: [f64; 3]| [p[0] * 0.01, p[2] * 0.01, -p[1] * 0.01].map(|v| v as f32);
    let pair = if reverse {
        [raw(segment.b), raw(segment.a)]
    } else {
        [raw(segment.a), raw(segment.b)]
    };
    pair.into_iter()
        .flatten()
        .enumerate()
        .fold(0, |mask, (i, v)| {
            mask | if v == 0. && v.is_sign_negative() {
                1 << i
            } else {
                0
            }
        })
}
fn displayed(p: Point, signs: u8) -> [f32; 3] {
    std::array::from_fn(|i| {
        if p[i] == 0 && signs & (1 << i) != 0 {
            -0.
        } else {
            f32::from_bits(p[i])
        }
    })
}
fn direction(a: Point, b: Point) -> [f64; 3] {
    let a = native(a);
    let b = native(b);
    let d: [f64; 3] = std::array::from_fn(|i| f64::from(b[i]) - f64::from(a[i]));
    let n = d.iter().map(|v| v * v).sum::<f64>().sqrt();
    d.map(|v| v / n)
}
fn length(key: EdgeKey) -> f64 {
    let a = native(key[0]);
    let b = native(key[1]);
    (0..3)
        .map(|i| {
            let d = f64::from(b[i]) - f64::from(a[i]);
            d * d
        })
        .sum::<f64>()
        .sqrt()
}

impl GrindPathIndex {
    pub fn build(identity: String, inputs: &[CellInput<'_>]) -> Result<Self, String> {
        let capacity = inputs.iter().try_fold(0usize, |n, input| {
            n.checked_add(input.segments.len())
                .filter(|&count| count <= LIMIT)
                .ok_or("Grind graph segment limit")
        })?;
        let mut index = Self {
            cells: HashMap::with_capacity(inputs.len()),
            lookup: HashMap::with_capacity(capacity),
            edges: Vec::with_capacity(capacity),
            free: BinaryHeap::new(),
            nodes: HashMap::new(),
            components: HashMap::new(),
            rails: BTreeMap::new(),
            raw_count: 0,
            next_owner: 1,
            identity: String::new(),
            serial: 0,
            pending: None,
            last_update: UpdateStats::default(),
            census: Census::default(),
            owner_rebound: false,
            delta_depth: 0,
        };
        let transaction = index.begin("", identity, inputs)?;
        index.commit(transaction)?;
        // Cold owners match the ordinal of the globally sorted legacy output.
        index.next_owner = 1;
        for rail in index.rails.values_mut() {
            rail.owner = index.next_owner;
            index.next_owner += 1;
        }
        index.delta_depth = 0;
        Ok(index)
    }
    pub fn snapshot_identity(&self) -> &str {
        &self.identity
    }
    pub fn rails(&self) -> Vec<Vec<[f32; 3]>> {
        self.rails.values().map(|r| (*r.points).clone()).collect()
    }
    pub fn rail_count(&self) -> usize {
        self.rails.len()
    }
    #[cfg(test)]
    pub fn assert_storage_integrity(&self) {
        let free: HashSet<_> = self.free.iter().copied().collect();
        assert_eq!(free.len(), self.free.len(), "duplicate vacant slot");
        for (id, edge) in self.edges.iter().enumerate() {
            assert_eq!(
                free.contains(&(id as u32)),
                edge.is_none(),
                "free/live slot {id}"
            );
            if let Some(edge) = edge {
                assert_eq!(self.lookup.get(&edge.key), Some(&(id as u32)));
                assert!(
                    self.components[&edge.component]
                        .edges
                        .contains(&(id as u32))
                );
                for at in edge.key {
                    assert!(self.nodes[&at].contains(&(id as u32)));
                }
            }
        }
        assert_eq!(self.lookup.len() + self.free.len(), self.edges.len());
    }
    pub fn begin(
        &mut self,
        expected: &str,
        identity: String,
        inputs: &[CellInput<'_>],
    ) -> Result<Transaction, String> {
        if self.pending.is_some() {
            return Err("Grind graph transaction already pending".into());
        }
        if self.identity != expected {
            return Err("Stale grind graph snapshot".into());
        }
        let mut keys = HashSet::with_capacity(inputs.len());
        let mut replacements = Vec::new();
        let mut stats = UpdateStats::default();
        let mut total = 0usize;
        // Preflight the entire update before touching accepted graph state.
        for input in inputs {
            if !keys.insert(input.key) {
                return Err("Duplicate grind graph cell".into());
            }
            total = total
                .checked_add(input.segments.len())
                .ok_or("Grind input count overflow")?;
            if total > LIMIT {
                return Err("Grind graph segment limit".into());
            }
            if let Some(old) = self.cells.get(&input.key) {
                if old.identity == input.identity {
                    if old.raw_count != input.segments.len() {
                        return Err("Grind cell identity count changed".into());
                    }
                    stats.reused_cells += 1;
                    continue;
                }
            }
            stats.input_segments_examined += input.segments.len();
            let mut edges = Vec::with_capacity(input.segments.len());
            for segment in input.segments {
                let a = point(segment.a)?;
                let b = point(segment.b)?;
                if a != b {
                    edges.push((
                        if a < b { [a, b] } else { [b, a] },
                        zero_signs(segment, a > b),
                    ));
                }
            }
            edges.sort_by_key(|(key, _)| *key);
            edges.dedup_by_key(|(key, _)| *key);
            let (edges, zero_signs) = edges.into_iter().unzip();
            replacements.push((
                input.key,
                Some(StagedCell {
                    identity: input.identity.into(),
                    raw_count: input.segments.len(),
                    edges,
                    zero_signs,
                }),
            ));
        }
        for key in self.cells.keys() {
            if !keys.contains(key) {
                replacements.push((*key, None));
            }
        }
        replacements.sort_unstable_by_key(|(key, _)| *key);
        stats.changed_cells = replacements.len();
        let undo = replacements
            .iter()
            .map(|(key, _)| {
                (
                    *key,
                    self.cells.get(key).map(|cell| StagedCell {
                        identity: cell.identity.clone(),
                        raw_count: cell.raw_count,
                        edges: cell.edges.iter().map(|&id| self.edge(id).key).collect(),
                        zero_signs: cell
                            .edges
                            .iter()
                            .map(|&id| self.edge(id).zero_signs)
                            .collect(),
                    }),
                )
            })
            .collect();
        let previous_identity = self.identity.clone();
        let next_owner = self.next_owner;
        let census = self.census;
        let delta_depth = self.delta_depth;
        let persisted_replacements = replacements.clone();
        let (delta, old_rails) = self.replace(replacements, &mut stats);
        self.serial = self.serial.wrapping_add(1);
        self.pending = Some(self.serial);
        self.identity = identity;
        self.delta_depth += 1;
        self.last_update = stats.clone();
        Ok(Transaction {
            delta,
            stats,
            serial: self.serial,
            previous_identity,
            undo,
            old_rails,
            next_owner,
            census,
            replacements: persisted_replacements,
            owner_rebound: self.owner_rebound,
            delta_depth,
            proposed_identity: self.identity.clone(),
        })
    }
    pub fn commit(&mut self, transaction: Transaction) -> Result<(), String> {
        if self.pending != Some(transaction.serial) {
            return Err("Stale grind graph commit".into());
        }
        self.pending = None;
        self.owner_rebound = false;
        Ok(())
    }
    pub fn rollback(&mut self, transaction: Transaction) -> Result<(), String> {
        if self.pending != Some(transaction.serial) {
            return Err("Stale grind graph rollback".into());
        }
        let mut stats = UpdateStats::default();
        self.replace(transaction.undo, &mut stats);
        // Only affected output is restored. In particular, accepted owners may
        // never be changed by a failed provider build or a stale scene job.
        for (seed, rail) in transaction.old_rails {
            *self.rails.get_mut(&seed).expect("rollback rail seed") = rail;
        }
        self.next_owner = transaction.next_owner;
        self.census = transaction.census;
        self.owner_rebound = transaction.owner_rebound;
        self.delta_depth = transaction.delta_depth;
        self.identity = transaction.previous_identity;
        self.pending = None;
        self.last_update = stats;
        Ok(())
    }
    fn edge(&self, id: u32) -> &Edge {
        self.edges[id as usize].as_ref().expect("live grind edge")
    }
    fn replace(
        &mut self,
        replacements: Vec<(CellKey, Option<StagedCell>)>,
        stats: &mut UpdateStats,
    ) -> (GraphDelta, BTreeMap<EdgeKey, Rail>) {
        let mut differences = HashMap::<EdgeKey, i64>::new();
        let mut signs = HashMap::<EdgeKey, u8>::new();
        for (key, new) in &replacements {
            if let Some(old) = self.cells.get(key) {
                for &id in &old.edges {
                    *differences.entry(self.edge(id).key).or_default() -= 1;
                }
            }
            if let Some(new) = new {
                for (&key, &zero_signs) in new.edges.iter().zip(&new.zero_signs) {
                    *differences.entry(key).or_default() += 1;
                    signs.entry(key).or_insert(zero_signs);
                }
            }
        }
        let mut touched = HashSet::new();
        let mut removed = Vec::new();
        let mut added = Vec::new();
        for (&key, &difference) in &differences {
            if difference == 0 {
                continue;
            }
            if let Some(&id) = self.lookup.get(&key) {
                let edge = self.edge(id);
                if i64::from(edge.refs) + difference == 0 {
                    touched.insert(edge.component);
                    removed.push(id);
                }
            } else {
                for at in key {
                    if let Some(around) = self.nodes.get(&at) {
                        for &id in around {
                            touched.insert(self.edge(id).component);
                        }
                    }
                }
                added.push(key);
            }
        }
        let mut candidates = Vec::new();
        let mut old_rails = BTreeMap::new();
        let mut delta = GraphDelta::default();
        let mut touched: Vec<_> = touched.into_iter().collect();
        touched.sort_unstable_by_key(|&id| self.edge(id).key);
        for id in touched {
            let component = self
                .components
                .remove(&id)
                .expect("touched grind component");
            if component.length >= 0.5 {
                self.census.retained -= 1;
                self.census.joints -= component.joints;
            } else {
                self.census.excluded -= 1;
                self.census.excluded_segments -= component.edges.len();
                self.census.excluded_length -= component.length;
            }
            for &seed_id in component.seeds.iter() {
                let seed = self.edge(seed_id).key;
                let rail = self.rails.remove(&seed).expect("component rail");
                self.census.provider_segments -= rail.points.len() - 1;
                delta.retired_owners.push(rail.owner);
                old_rails.insert(seed, rail);
            }
            candidates.extend(component.edges.iter().copied());
        }
        for (key, _) in &replacements {
            if let Some(old) = self.cells.remove(key) {
                self.raw_count -= old.raw_count;
            }
        }
        let mut dirty_nodes = HashSet::new();
        for id in removed {
            let edge = self.edges[id as usize].take().unwrap();
            self.lookup.remove(&edge.key);
            self.free.push(id);
            for at in edge.key {
                let around = self.nodes.get_mut(&at).unwrap();
                if around.remove(id) {
                    self.nodes.remove(&at);
                }
                dirty_nodes.insert(at);
            }
            stats.topology_edges_removed += 1;
        }
        // Filter old candidates before reusing freed slots for unrelated edges.
        candidates.retain(|&id| self.edges[id as usize].is_some());
        for (&key, &difference) in &differences {
            if let Some(&id) = self.lookup.get(&key) {
                let edge = self.edges[id as usize].as_mut().unwrap();
                edge.refs = (i64::from(edge.refs) + difference) as u32;
            }
        }
        added.sort_unstable();
        for key in added {
            let edge = Edge {
                key,
                refs: differences[&key] as u32,
                component: UNASSIGNED,
                zero_signs: signs[&key],
            };
            let id = if let Some(id) = self.free.pop() {
                self.edges[id as usize] = Some(edge);
                id
            } else {
                let id = self.edges.len() as u32;
                self.edges.push(Some(edge));
                id
            };
            self.lookup.insert(key, id);
            candidates.push(id);
            for at in key {
                self.nodes
                    .entry(at)
                    .and_modify(|around| around.push(id))
                    .or_insert(Around::One(id));
                dirty_nodes.insert(at);
            }
            stats.topology_edges_added += 1;
        }
        for (key, new) in replacements {
            if let Some(new) = new {
                self.raw_count += new.raw_count;
                let ids = new.edges.into_iter().map(|key| self.lookup[&key]).collect();
                self.cells.insert(
                    key,
                    Cell {
                        identity: new.identity,
                        raw_count: new.raw_count,
                        edges: ids,
                    },
                );
            }
        }
        for at in dirty_nodes {
            if let Some(around) = self.nodes.get_mut(&at) {
                around.sort_unstable_by_key(|&id| self.edges[id as usize].as_ref().unwrap().key);
            }
        }
        candidates.sort_unstable_by_key(|&id| self.edge(id).key);
        candidates.dedup();
        for &id in &candidates {
            self.edges[id as usize].as_mut().unwrap().component = UNASSIGNED;
        }
        let mut new_seeds = Vec::new();
        for seed in candidates {
            if self.edge(seed).component != UNASSIGNED {
                continue;
            }
            let mut members = vec![seed];
            self.edges[seed as usize].as_mut().unwrap().component = seed;
            let mut cursor = 0;
            let mut arc_length = 0.;
            while cursor < members.len() {
                let key = self.edge(members[cursor]).key;
                arc_length += length(key);
                for at in key {
                    for &next in &self.nodes[&at] {
                        if self.edges[next as usize].as_ref().unwrap().component == UNASSIGNED {
                            self.edges[next as usize].as_mut().unwrap().component = seed;
                            members.push(next);
                        }
                    }
                }
                cursor += 1;
            }
            stats.rebuilt_components += 1;
            stats.rebuilt_component_edges += members.len();
            members.sort_unstable_by_key(|&id| self.edge(id).key);
            let (seeds, joints) = if arc_length >= 0.5 {
                self.stitch(&members)
            } else {
                (Vec::new(), 0)
            };
            if arc_length >= 0.5 {
                self.census.retained += 1;
                self.census.joints += joints;
            } else {
                self.census.excluded += 1;
                self.census.excluded_segments += members.len();
                self.census.excluded_length += arc_length;
            }
            new_seeds.extend(seeds.iter().map(|&id| self.edge(id).key));
            self.components.insert(
                seed,
                Component {
                    edges: members.into(),
                    seeds: seeds.into_boxed_slice(),
                    length: arc_length,
                    joints,
                },
            );
        }
        new_seeds.sort_unstable();
        for seed in new_seeds {
            let rail = self.rails.get_mut(&seed).unwrap();
            rail.owner = self.next_owner;
            self.next_owner += 1;
            if !self.identity.is_empty() {
                delta.rails.push((rail.owner, (*rail.points).clone()));
            }
        }
        delta.retired_owners.sort_unstable();
        (delta, old_rails)
    }
    fn stitch(&mut self, members: &[u32]) -> (Vec<u32>, usize) {
        if let [id] = members {
            let edge = self.edge(*id);
            let key = edge.key;
            let signs = edge.zero_signs;
            self.rails.insert(
                key,
                Rail {
                    owner: 0,
                    points: Arc::new(vec![
                        displayed(key[0], signs),
                        displayed(key[1], signs >> 3),
                    ]),
                },
            );
            self.census.provider_segments += 1;
            return (vec![*id], 0);
        }
        let mut used = HashSet::with_capacity(members.len());
        let mut seeds = Vec::new();
        let mut joints = 0;
        for &id in members {
            if !used.insert(id) {
                continue;
            }
            let first = self.edge(id).key;
            let signs = self.edge(id).zero_signs;
            let mut line =
                VecDeque::from([displayed(first[0], signs), displayed(first[1], signs >> 3)]);
            for backwards in [false, true] {
                let (mut at, mut heading) = if backwards {
                    (first[0], direction(first[1], first[0]))
                } else {
                    (first[1], direction(first[0], first[1]))
                };
                loop {
                    let around = &self.nodes[&at];
                    if around.len() != 2 {
                        break;
                    }
                    let Some(&next) = around.iter().find(|id| !used.contains(*id)) else {
                        break;
                    };
                    let key = self.edge(next).key;
                    let end = if key[0] == at { 1 } else { 0 };
                    let to = key[end];
                    let d = direction(at, to);
                    if (0..3).map(|i| heading[i] * d[i]).sum::<f64>() < 0.82 {
                        break;
                    }
                    used.insert(next);
                    joints += 1;
                    let output = displayed(to, self.edge(next).zero_signs >> (3 * end));
                    if backwards {
                        line.push_front(output);
                    } else {
                        line.push_back(output);
                    }
                    at = to;
                    heading = d;
                }
            }
            let points: Vec<[f32; 3]> = line.into_iter().collect();
            self.census.provider_segments += points.len() - 1;
            seeds.push(id);
            self.rails.insert(
                first,
                Rail {
                    owner: 0,
                    points: Arc::new(points),
                },
            );
        }
        (seeds, joints)
    }
    pub fn stats(&self) -> Value {
        json!({"unique_segments":self.lookup.len(),"duplicate_segments":self.raw_count-self.lookup.len(),
            "stitched_joints":self.census.joints,"provider_segments":self.census.provider_segments,"minimum_connected_component_cm":50,
            "retained_components":self.census.retained,"excluded_components":self.census.excluded,"excluded_segments":self.census.excluded_segments,
            "excluded_arclength_cm":self.census.excluded_length*100.,"incremental_graph_version":VERSION,
            "changed_cells":self.last_update.changed_cells,"reused_cells":self.last_update.reused_cells,
            "input_segments_examined":self.last_update.input_segments_examined,
            "topology_edges_added":self.last_update.topology_edges_added,"topology_edges_removed":self.last_update.topology_edges_removed,
            "rebuilt_components":self.last_update.rebuilt_components,"rebuilt_component_edges":self.last_update.rebuilt_component_edges})
    }
}
