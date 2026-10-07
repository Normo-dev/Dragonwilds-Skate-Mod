//! Static stock assets and explicit authored imports. Stock registration follows
//! source occurrence order; imported edits retain the original native base tree
//! and append immutable native overlays with retired-owner tombstones.
use super::{
    octree::{Bounds, Octree},
    spline,
};
use serde_json::Value;
use skate_core::physics::grind_contact::Primitive;
use skate_data::skate_map::SkateMap;
use std::collections::{BinaryHeap, HashMap};
#[path = "provider/incremental.rs"]
mod incremental;
use incremental::Incremental;

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub(crate) struct SourceIdentity {
    pub stream_file: String,
    pub asset_id: String,
    pub section_index: u64,
    pub section_offset: u64,
}
struct Asset {
    source: SourceIdentity,
    bounds: Bounds,
    indices: Vec<usize>,
    tree: Octree,
}
pub(crate) struct StaticProvider {
    incremental: Option<Incremental>,
    host_authored: bool,
    dense_authored: bool,
    primitives: Vec<Primitive>,
    metadata: Vec<spline::PrimitiveMetadata>,
    rail_guids: Vec<[u64; 2]>,
    assets: Vec<Asset>,
    /// Parallel to primitives; no endpoint-derived replacement boxes.
    authored_bounds: Vec<Bounds>,
    source_for_primitive: Vec<usize>,
    source_rail_indices: Vec<u64>,
}

impl StaticProvider {
    /// WMET preserves source section identities even when spline IDs repeat.
    /// Missing provenance is an explicit conversion prerequisite, not a ray miss.
    pub fn new(map: Option<&SkateMap>) -> Result<Self, String> {
        let Some(map) = map else {
            return Ok(Self {
                incremental: None,
                host_authored: false,
                dense_authored: false,
                primitives: vec![],
                metadata: vec![],
                rail_guids: vec![],
                assets: vec![],
                authored_bounds: vec![],
                source_for_primitive: vec![],
                source_rail_indices: vec![],
            });
        };
        if map.rails.is_empty() {
            return Ok(Self {
                incremental: None,
                host_authored: false,
                dense_authored: false,
                primitives: vec![],
                metadata: vec![],
                rail_guids: vec![],
                assets: vec![],
                authored_bounds: vec![],
                source_for_primitive: vec![],
                source_rail_indices: vec![],
            });
        }
        if map.rails.iter().all(|rail| rail.native.is_none()) {
            return Self::authored(&map.rails);
        }
        let mut metadata = map.extensions.iter().filter(|e| e.tag == *b"WMET");
        let extension = metadata
            .next()
            .ok_or("Stock grind provider requires WMET source identity metadata")?;
        if extension.schema != 1 || metadata.next().is_some() {
            return Err("Expected exactly one WMET schema 1 extension".into());
        }
        let manifest: Value =
            serde_json::from_slice(&extension.payload).map_err(|e| format!("Invalid WMET: {e}"))?;
        if manifest["grind_coordinate_policy"]["mode"].as_str() != Some("world_space") {
            return Err(
                "Static grind provider requires verified world_space spline payloads".into(),
            );
        }
        let records = manifest["grind_splines"]
            .as_array()
            .ok_or("WMET grind_splines array missing")?;
        if records.len() != map.rails.len() {
            return Err("WMET/package rail count mismatch".into());
        }
        let bytes = spline::build(Some(map))?;
        let (primitives, metadata) = spline::decoded_from_blob(&bytes)?;
        let word = |at: usize| u32::from_be_bytes(bytes[at..at + 4].try_into().unwrap());
        let mut grouped: Vec<(SourceIdentity, Vec<usize>)> = Vec::new();
        let mut source_index = HashMap::new();
        let mut authored_bounds = Vec::with_capacity(primitives.len());
        let mut spatial_bounds = Vec::with_capacity(primitives.len());
        let mut source_for_primitive = Vec::with_capacity(primitives.len());
        let mut source_rail_indices = Vec::with_capacity(primitives.len());
        let mut rail_guids = Vec::with_capacity(map.rails.len());
        let mut ordinal = 0;
        for (rail, (record, package_rail)) in records.iter().zip(&map.rails).enumerate() {
            if package_rail.native.is_none() {
                return Err(format!(
                    "Stock rail {} lacks native cubic payload",
                    package_rail.name
                ));
            }
            let source = SourceIdentity {
                stream_file: string(record, "stream_file")?,
                asset_id: string(record, "asset_id")?,
                section_index: number(record, "section_index")?,
                section_offset: number(record, "section_offset")?,
            };
            let source_rail = number(record, "rail_index")?;
            let expected_name = format!(
                "{}_{}_{}",
                source.asset_id, source.section_index, source_rail
            );
            if package_rail.name != expected_name {
                return Err(format!(
                    "Stock rail provenance/name mismatch: {}",
                    package_rail.name
                ));
            }
            let header = 16 + rail * 32;
            let first = word(header + 20) as usize;
            let last = word(header + 24) as usize;
            let count = (last - first) / 144 + 1;
            let spline_id = ((word(header) as u64) << 32) | word(header + 4) as u64;
            let type_signature = ((word(header + 8) as u64) << 32) | word(header + 12) as u64;
            rail_guids.push([spline_id, type_signature]);
            if parse_id(record, "spline_id")? != spline_id
                || number(record, "segment_count")? != count as u64
                || parse_id(record, "type_signature")? != type_signature
                || number(record, "flags")? != word(header + 16) as u64
                || number(record, "trailing_word")? != word(header + 28) as u64
                || record["closed"].as_bool() != Some(package_rail.closed)
            {
                return Err(format!(
                    "Stock rail native identity/count mismatch: {}",
                    package_rail.name
                ));
            }
            let asset = *source_index.entry(source.clone()).or_insert_with(|| {
                let index = grouped.len();
                grouped.push((source, Vec::new()));
                index
            });
            for segment in 0..count {
                let at = first + 144 * segment;
                let bounds = Bounds {
                    min: std::array::from_fn(|i| f32::from_bits(word(at + 80 + i * 4))),
                    max: std::array::from_fn(|i| f32::from_bits(word(at + 96 + i * 4))),
                };
                authored_bounds.push(bounds);
                spatial_bounds.push(bounds.identity_transformed());
                grouped[asset].1.push(ordinal);
                source_for_primitive.push(asset);
                source_rail_indices.push(source_rail);
                ordinal += 1;
            }
        }
        let assets = grouped
            .into_iter()
            .map(|(source, indices)| {
                let bounds = indices
                    .iter()
                    .map(|&i| spatial_bounds[i])
                    .reduce(Bounds::union)
                    .ok_or("Empty stock grind source section")?
                    .padded();
                let tree =
                    Octree::new(bounds, indices.iter().map(|&i| spatial_bounds[i]).collect())?;
                Ok(Asset {
                    source,
                    bounds,
                    indices,
                    tree,
                })
            })
            .collect::<Result<_, String>>()?;
        Ok(Self {
            incremental: None,
            host_authored: false,
            dense_authored: false,
            primitives,
            metadata,
            rail_guids,
            assets,
            authored_bounds,
            source_for_primitive,
            source_rail_indices,
        })
    }

    /// Explicit host-authored polylines have no retail source section identity.
    pub fn authored(rails: &[skate_data::skate_map::Rail]) -> Result<Self, String> {
        Self::authored_chunked(rails, usize::from(u16::MAX))
    }

    /// Temporary Pegasus blobs retain their native count/offset format. The
    /// decoded runtime arrays use global owners and one shared octree, so blob
    /// boundaries cannot change candidate ordering or the native forty cap.
    pub(super) fn authored_chunked(
        rails: &[skate_data::skate_map::Rail],
        chunk_size: usize,
    ) -> Result<Self, String> {
        if chunk_size == 0 || chunk_size > usize::from(u16::MAX) {
            return Err("Invalid authored spline conversion chunk size".into());
        }
        let mut primitives = Vec::new();
        let mut metadata = Vec::new();
        let mut rail_guids = Vec::with_capacity(rails.len());
        let mut owner_offset = 0u64;
        for chunk in rails.chunks(chunk_size) {
            let bytes = spline::build_rails(chunk)?;
            let (mut entries, side_metadata) = spline::decoded_from_blob(&bytes)?;
            for entry in &mut entries {
                entry.owner = entry
                    .owner
                    .checked_add(owner_offset)
                    .ok_or("Spline owner overflow")?;
            }
            primitives.extend(entries);
            metadata.extend(side_metadata);
            rail_guids.extend((0..chunk.len()).map(|i| {
                let at = 16 + i * 32;
                [
                    u64::from_be_bytes(bytes[at..at + 8].try_into().unwrap()),
                    u64::from_be_bytes(bytes[at + 8..at + 16].try_into().unwrap()),
                ]
            }));
            owner_offset = owner_offset
                .checked_add(chunk.len() as u64)
                .ok_or("Spline owner overflow")?;
        }
        let authored_bounds: Vec<_> = primitives
            .iter()
            .map(|p| Bounds {
                min: std::array::from_fn(|i| p.start[i].min(p.end[i])),
                max: std::array::from_fn(|i| p.start[i].max(p.end[i])),
            })
            .collect();
        let assets = if let Some(bounds) = authored_bounds.iter().copied().reduce(Bounds::union) {
            let bounds = bounds.padded();
            vec![Asset {
                source: SourceIdentity {
                    stream_file: String::new(),
                    asset_id: "host-authored".into(),
                    section_index: 0,
                    section_offset: 0,
                },
                bounds,
                indices: (0..primitives.len()).collect(),
                tree: Octree::new(bounds, authored_bounds.clone())?,
            }]
        } else {
            vec![]
        };
        let source_rail_indices = primitives.iter().map(|p| p.owner - 1).collect();
        eprintln!(
            "SOURCE_GRIND_INDEX rails={} segments={} assets={}",
            rails.len(),
            primitives.len(),
            assets.len()
        );
        Ok(Self {
            incremental: None,
            host_authored: true,
            dense_authored: false,
            source_for_primitive: vec![0; primitives.len()],
            primitives,
            metadata,
            rail_guids,
            assets,
            authored_bounds,
            source_rail_indices,
        })
    }

    /// Opt-in host admission for the exposed-edge world only. Retail providers
    /// and ordinary authored/legacy constructors keep the original first40.
    pub(crate) fn enable_dense_authored(&mut self) -> Result<(), String> {
        if !self.primitives.is_empty()
            && (!self.host_authored
                || self.assets.len() != 1
                || self.assets[0].source.stream_file != ""
                || self.assets[0].source.asset_id != "host-authored"
                || self.assets[0].source.section_index != 0
                || self.assets[0].source.section_offset != 0)
        {
            return Err("Dense grind admission requires a sole host-authored provider".into());
        }
        self.dense_authored = true;
        self.host_authored = true;
        Ok(())
    }

    /// Contiguous original asset storage, used by extraction fixtures. Runtime
    /// consumers use `primitive` and `primitive_count` because imported scene
    /// updates also contain immutable overlays with stable sparse handles.
    pub fn primitives(&self) -> &[Primitive] {
        &self.primitives
    }

    /// Stable handles also address immutable scene overlays. Retired owners
    /// cannot be resolved through a handle retained by a previous tick.
    pub fn primitive(&self, primitive: usize) -> Option<Primitive> {
        match &self.incremental {
            Some(index) => index.primitive(primitive),
            None => self.primitives.get(primitive).copied(),
        }
    }

    pub fn primitive_count(&self) -> usize {
        self.incremental
            .as_ref()
            .map_or(self.primitives.len(), |index| index.count)
    }

    pub(crate) fn is_exposed(&self) -> bool { self.host_authored && self.dense_authored }

    pub fn metadata(&self, primitive: usize) -> Option<&spline::PrimitiveMetadata> {
        if let Some(index) = &self.incremental {
            return index.metadata(primitive);
        }
        self.metadata.get(primitive)
    }

    /// Resolve contact's map-local header handle without confusing it with GUID.
    pub fn spline_guids(&self, owner: u64) -> Option<[u64; 2]> {
        if let Some(index) = &self.incremental {
            return index.spline_guids(owner);
        }
        let rail = usize::try_from(owner.checked_sub(1)?).ok()?;
        self.rail_guids.get(rail).copied()
    }

    pub fn source_rail_index(&self, primitive: usize) -> Option<u64> {
        if self.incremental.is_some() {
            return self.primitive(primitive).map(|p| p.owner - 1);
        }
        self.source_rail_indices.get(primitive).copied()
    }

    pub fn source(&self, primitive: usize) -> Option<&SourceIdentity> {
        if let Some(index) = &self.incremental {
            return index.source(primitive);
        }
        self.source_for_primitive
            .get(primitive)
            .map(|&asset| &self.assets[asset].source)
    }

    pub fn bounds(&self, primitive: usize) -> Option<([f32; 3], [f32; 3])> {
        if let Some(index) = &self.incremental {
            return index.bounds(primitive);
        }
        self.authored_bounds.get(primitive).map(|b| (b.min, b.max))
    }

    /// S3 82C1EAD8 static pass only: query gate, ordered asset overlap and
    /// native per-asset octree traversal, at most forty original vector indices.
    /// Does not implement or pretend to query unavailable moving providers.
    pub fn query(&self, min: [f32; 3], max: [f32; 3]) -> Result<Vec<usize>, String> {
        if (0..3).any(|i| !min[i].is_finite() || !max[i].is_finite() || min[i] > max[i]) {
            return Err("Invalid grind query bounds".into());
        }
        let query = Bounds { min, max };
        let delta: [f32; 3] = std::array::from_fn(|i| min[i] - max[i]);
        let square = (delta[0] * delta[0] + delta[1] * delta[1]) + delta[2] * delta[2];
        if !(square > f32::from_bits(0x3780_0000)) {
            return Ok(vec![]);
        }
        if let Some(index) = &self.incremental {
            return Ok(index.query(query));
        }
        if self.dense_authored {
            // f64 also handles valid finite f32 boxes whose midpoint/distance
            // arithmetic would overflow f32. Nonnegative finite f64 bit order
            // is numerical order; the max-heap retains only the best40 keys.
            let center: [f64; 3] =
                std::array::from_fn(|i| (f64::from(min[i]) + f64::from(max[i])) * 0.5);
            let mut best = BinaryHeap::<(u64, usize, usize)>::with_capacity(40);
            let mut ordinal = 0usize;
            for asset in &self.assets {
                if !asset.bounds.overlaps(query) {
                    continue;
                }
                asset.tree.visit(query, |local| {
                    let id = asset.indices[local];
                    let p = self.primitives[id];
                    let a: [f64; 3] = std::array::from_fn(|i| f64::from(p.start[i]));
                    let d: [f64; 3] = std::array::from_fn(|i| f64::from(p.end[i]) - a[i]);
                    let square: f64 = d.iter().map(|v| v * v).sum();
                    let t = if square > 0. {
                        ((0..3).map(|i| (center[i] - a[i]) * d[i]).sum::<f64>() / square)
                            .clamp(0., 1.)
                    } else {
                        0.
                    };
                    let distance: f64 = (0..3)
                        .map(|i| {
                            let v = center[i] - (a[i] + d[i] * t);
                            v * v
                        })
                        .sum();
                    let entry = (distance.to_bits(), id, ordinal);
                    if best.len() < 40 {
                        best.push(entry);
                    } else if best.peek().is_some_and(|worst| entry < *worst) {
                        best.pop();
                        best.push(entry);
                    }
                    ordinal += 1;
                    true
                });
            }
            let mut selected = best.into_vec();
            selected.sort_unstable_by_key(|entry| entry.2);
            return Ok(selected.into_iter().map(|entry| entry.1).collect());
        }
        let mut result = Vec::new();
        for asset in &self.assets {
            if !asset.bounds.overlaps(query) {
                continue;
            }
            for local in asset.tree.query(query, 40 - result.len()) {
                result.push(asset.indices[local]);
            }
            if result.len() == 40 {
                break;
            }
        }
        Ok(result)
    }

    /// Spatially gather the same source-vector iteration used by handplant
    /// and footplant. The optional cap is applied after source-order sorting;
    /// footplant deliberately supplies no cap.
    pub fn query_source_order(
        &self,
        min: [f32; 3],
        max: [f32; 3],
        limit: Option<usize>,
    ) -> Result<Vec<Primitive>, String> {
        if (0..3).any(|i| !min[i].is_finite() || !max[i].is_finite() || min[i] > max[i]) {
            return Err("Invalid grind query bounds".into());
        }
        let query = Bounds { min, max };
        if !self.host_authored || !self.dense_authored {
            let count = limit.unwrap_or(usize::MAX);
            return Ok(self
                .primitives
                .iter()
                .copied()
                .filter(|p| {
                    (0..3).all(|i| {
                        p.start[i].min(p.end[i]) <= max[i] && p.start[i].max(p.end[i]) >= min[i]
                    })
                })
                .take(count)
                .collect());
        }
        let mut indices = Vec::new();
        let mut first = BinaryHeap::<usize>::with_capacity(limit.unwrap_or(0));
        let mut accept = |id| {
            if let Some(limit) = limit {
                if first.len() < limit {
                    first.push(id);
                } else if first.peek().is_some_and(|worst| id < *worst) {
                    first.pop();
                    first.push(id);
                }
            } else {
                indices.push(id);
            }
        };
        if let Some(index) = &self.incremental {
            index.visit(query, |id, _| {
                accept(id);
            });
        } else {
            for asset in &self.assets {
                if asset.bounds.overlaps(query) {
                    asset.tree.visit(query, |local| {
                        accept(asset.indices[local]);
                        true
                    });
                }
            }
        }
        if limit.is_some() {
            indices = first.into_vec();
        }
        indices.sort_unstable();
        if let Some(limit) = limit {
            indices.truncate(limit);
        }
        Ok(indices
            .into_iter()
            .filter_map(|id| self.primitive(id))
            .collect())
    }
}

fn string(value: &Value, key: &str) -> Result<String, String> {
    value[key]
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("WMET rail missing string {key}"))
}
fn number(value: &Value, key: &str) -> Result<u64, String> {
    value[key]
        .as_u64()
        .ok_or_else(|| format!("WMET rail missing unsigned {key}"))
}
fn parse_id(value: &Value, key: &str) -> Result<u64, String> {
    let value = string(value, key)?;
    u64::from_str_radix(value.strip_prefix("0x").unwrap_or(&value), 16)
        .map_err(|_| format!("Invalid WMET {key}"))
}
