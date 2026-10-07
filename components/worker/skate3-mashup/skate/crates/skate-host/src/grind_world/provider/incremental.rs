//! Imported scenes retain the original global native tree. A scene edit only
//! builds an immutable tree for its changed rails, and path-copies the compact
//! retired-owner set. Every query still applies the dense world's global forty
//! nearest admission, then emits retained-base and overlay native traversal.
use super::{Bounds, Primitive, SourceIdentity, StaticProvider, spline};
use std::{collections::BinaryHeap, sync::Arc};

#[derive(Clone, Default)]
struct OwnerSet(Option<Arc<OwnerNode>>);
enum OwnerNode {
    Leaf(u64),
    Branch {
        bit: u32,
        zero: Arc<OwnerNode>,
        one: Arc<OwnerNode>,
    },
}
impl OwnerSet {
    fn contains(&self, owner: u64) -> bool {
        let Some(mut node) = self.0.as_deref() else {
            return false;
        };
        loop {
            match node {
                OwnerNode::Leaf(value) => return *value == owner,
                OwnerNode::Branch { bit, zero, one } => {
                    node = if owner & (1u64 << bit) == 0 {
                        zero
                    } else {
                        one
                    };
                }
            }
        }
    }
    fn insert(&mut self, owner: u64) {
        fn representative(mut node: &OwnerNode) -> u64 {
            loop {
                match node {
                    OwnerNode::Leaf(value) => return *value,
                    OwnerNode::Branch { zero, .. } => node = zero,
                }
            }
        }
        fn insert(node: &Arc<OwnerNode>, owner: u64) -> Arc<OwnerNode> {
            let different = representative(node) ^ owner;
            if different == 0 {
                return Arc::clone(node);
            }
            let bit = 63 - different.leading_zeros();
            match node.as_ref() {
                OwnerNode::Branch {
                    bit: existing,
                    zero,
                    one,
                } if *existing >= bit => {
                    if owner & (1u64 << existing) == 0 {
                        Arc::new(OwnerNode::Branch {
                            bit: *existing,
                            zero: insert(zero, owner),
                            one: Arc::clone(one),
                        })
                    } else {
                        Arc::new(OwnerNode::Branch {
                            bit: *existing,
                            zero: Arc::clone(zero),
                            one: insert(one, owner),
                        })
                    }
                }
                _ => {
                    let leaf = Arc::new(OwnerNode::Leaf(owner));
                    let (zero, one) = if owner & (1u64 << bit) == 0 {
                        (leaf, Arc::clone(node))
                    } else {
                        (Arc::clone(node), leaf)
                    };
                    Arc::new(OwnerNode::Branch { bit, zero, one })
                }
            }
        }
        self.0 = Some(match &self.0 {
            Some(node) => insert(node, owner),
            None => Arc::new(OwnerNode::Leaf(owner)),
        });
    }
}

#[derive(Clone)]
struct Overlay {
    first: usize,
    provider: Arc<StaticProvider>,
    owners: Arc<Vec<u64>>,
    active_owners: usize,
}
pub(super) struct Incremental {
    base: Arc<StaticProvider>,
    overlays: Vec<Overlay>,
    retired: OwnerSet,
    next_index: usize,
    next_owner: u64,
    pub(super) count: usize,
}
impl StaticProvider {
    pub(crate) fn exposed_delta(
        previous: Arc<Self>,
        retired_owners: Vec<u64>,
        rails: Vec<(u64, Vec<[f32; 3]>)>,
    ) -> Result<Arc<Self>, String> {
        let started = std::time::Instant::now();
        let retired_count = retired_owners.len();
        let added_rails = rails.len();
        if !previous.host_authored || !previous.dense_authored {
            return Err(
                "Incremental grind updates require the dense imported authored provider".into(),
            );
        }
        let mut index = if let Some(prior) = &previous.incremental {
            Incremental {
                base: Arc::clone(&prior.base),
                overlays: prior.overlays.clone(),
                retired: prior.retired.clone(),
                next_index: prior.next_index,
                next_owner: prior.next_owner,
                count: prior.count,
            }
        } else {
            Incremental {
                next_index: previous.primitives.len(),
                next_owner: (previous.rail_guids.len() as u64)
                    .checked_add(1)
                    .ok_or("Grind owner overflow")?,
                count: previous.primitives.len(),
                base: Arc::clone(&previous),
                overlays: Vec::new(),
                retired: OwnerSet::default(),
            }
        };
        // Validation and conversion only mutate this uninstalled snapshot.
        for owner in retired_owners {
            if owner == 0 || index.retired.contains(owner) {
                return Err("Retiring an absent grind owner".into());
            }
            let count = index.owner_segments(owner);
            if count == 0 {
                return Err("Retiring an absent grind owner".into());
            }
            index.count = index
                .count
                .checked_sub(count)
                .ok_or("Grind primitive count underflow")?;
            index.retired.insert(owner);
            if owner > index.base.rail_guids.len() as u64 {
                let at = index
                    .overlay_index(owner)
                    .ok_or("Retired overlay owner lacks storage")?;
                let overlay = &mut index.overlays[at];
                overlay.active_owners = overlay
                    .active_owners
                    .checked_sub(1)
                    .ok_or("Grind owner count underflow")?;
            }
        }
        // Completely replaced temporary tables have no active handles. The
        // previous prepared snapshot keeps them alive until ordinary retirement.
        index.overlays.retain(|overlay| overlay.active_owners != 0);
        if !rails.is_empty() {
            let mut owners = Vec::with_capacity(rails.len());
            let mut authored = Vec::with_capacity(rails.len());
            for (owner, points) in rails {
                if owner < index.next_owner {
                    return Err("New grind owners must be fresh and strictly increasing".into());
                }
                index.next_owner = owner.checked_add(1).ok_or("Grind owner overflow")?;
                owners.push(owner);
                authored.push(skate_data::skate_map::Rail {
                    name: format!("iw4_edge_{}", owner - 1),
                    closed: false,
                    points,
                    native: None,
                });
            }
            let mut provider = Self::authored(&authored)?;
            for primitive in &mut provider.primitives {
                primitive.owner = owners[(primitive.owner - 1) as usize];
            }
            for (source, primitive) in provider
                .source_rail_indices
                .iter_mut()
                .zip(&provider.primitives)
            {
                *source = primitive.owner - 1;
            }
            let count = provider.primitives.len();
            index.count = index
                .count
                .checked_add(count)
                .ok_or("Grind primitive count overflow")?;
            let first = index.next_index;
            index.next_index = first
                .checked_add(count)
                .ok_or("Grind primitive handle overflow")?;
            index.overlays.push(Overlay {
                first,
                provider: Arc::new(provider),
                active_owners: owners.len(),
                owners: Arc::new(owners),
            });
        }
        let mut provider = Self::new(None)?;
        provider.host_authored = true;
        provider.dense_authored = true;
        eprintln!(
            "SOURCE_GRIND_DELTA base_segments={} active_segments={} retired_owners={} added_rails={} added_segments={} overlays={} elapsed_ms={:.3}",
            index.base.primitives.len(),
            index.count,
            retired_count,
            added_rails,
            if added_rails == 0 {
                0
            } else {
                index
                    .overlays
                    .last()
                    .map_or(0, |overlay| overlay.provider.primitives.len())
            },
            index.overlays.len(),
            started.elapsed().as_secs_f64() * 1000.
        );
        provider.incremental = Some(index);
        Ok(Arc::new(provider))
    }
}
impl Incremental {
    fn overlay_index(&self, owner: u64) -> Option<usize> {
        let at = self
            .overlays
            .partition_point(|overlay| overlay.owners.last().is_some_and(|last| *last < owner));
        let overlay = self.overlays.get(at)?;
        overlay.owners.binary_search(&owner).ok().map(|_| at)
    }
    fn resolve(&self, index: usize) -> Option<(&StaticProvider, usize)> {
        if index < self.base.primitives.len() {
            return Some((&self.base, index));
        }
        let at = self
            .overlays
            .partition_point(|overlay| overlay.first <= index)
            .checked_sub(1)?;
        let overlay = &self.overlays[at];
        let local = index - overlay.first;
        (local < overlay.provider.primitives.len()).then_some((&overlay.provider, local))
    }
    pub(super) fn primitive(&self, index: usize) -> Option<Primitive> {
        let (provider, local) = self.resolve(index)?;
        let primitive = provider.primitives[local];
        (!self.retired.contains(primitive.owner)).then_some(primitive)
    }
    pub(super) fn metadata(&self, index: usize) -> Option<&spline::PrimitiveMetadata> {
        self.primitive(index)?;
        let (provider, local) = self.resolve(index)?;
        provider.metadata.get(local)
    }
    pub(super) fn source(&self, index: usize) -> Option<&SourceIdentity> {
        self.primitive(index)?;
        let (provider, local) = self.resolve(index)?;
        provider.source(local)
    }
    pub(super) fn bounds(&self, index: usize) -> Option<([f32; 3], [f32; 3])> {
        self.primitive(index)?;
        let (provider, local) = self.resolve(index)?;
        provider.bounds(local)
    }
    pub(super) fn spline_guids(&self, owner: u64) -> Option<[u64; 2]> {
        if self.retired.contains(owner) {
            return None;
        }
        if owner <= self.base.rail_guids.len() as u64 {
            return self.base.spline_guids(owner);
        }
        let overlay = &self.overlays[self.overlay_index(owner)?];
        let local = overlay.owners.binary_search(&owner).ok()?;
        overlay.provider.rail_guids.get(local).copied()
    }
    fn owner_segments(&self, owner: u64) -> usize {
        let provider = if owner <= self.base.rail_guids.len() as u64 {
            &self.base
        } else if let Some(at) = self.overlay_index(owner) {
            &self.overlays[at].provider
        } else {
            return 0;
        };
        let first = provider
            .primitives
            .partition_point(|primitive| primitive.owner < owner);
        let end = provider
            .primitives
            .partition_point(|primitive| primitive.owner <= owner);
        end - first
    }
    pub(super) fn visit(&self, query: Bounds, mut accept: impl FnMut(usize, Primitive)) {
        let mut visit_provider = |provider: &StaticProvider, first: usize| {
            for asset in &provider.assets {
                if !asset.bounds.overlaps(query) {
                    continue;
                }
                asset.tree.visit(query, |local| {
                    let index = asset.indices[local];
                    let primitive = provider.primitives[index];
                    if !self.retired.contains(primitive.owner) {
                        accept(first + index, primitive);
                    }
                    true
                });
            }
        };
        visit_provider(&self.base, 0);
        for overlay in &self.overlays {
            visit_provider(&overlay.provider, overlay.first);
        }
    }
    pub(super) fn query(&self, query: Bounds) -> Vec<usize> {
        let center: [f64; 3] =
            std::array::from_fn(|i| (f64::from(query.min[i]) + f64::from(query.max[i])) * 0.5);
        let mut best = BinaryHeap::<(u64, usize, usize)>::with_capacity(40);
        let mut ordinal = 0;
        self.visit(query, |index, primitive| {
            let a: [f64; 3] = std::array::from_fn(|i| f64::from(primitive.start[i]));
            let d: [f64; 3] = std::array::from_fn(|i| f64::from(primitive.end[i]) - a[i]);
            let square: f64 = d.iter().map(|v| v * v).sum();
            let t = if square > 0. {
                ((0..3).map(|i| (center[i] - a[i]) * d[i]).sum::<f64>() / square).clamp(0., 1.)
            } else {
                0.
            };
            let distance: f64 = (0..3)
                .map(|i| {
                    let v = center[i] - (a[i] + d[i] * t);
                    v * v
                })
                .sum();
            let entry = (distance.to_bits(), index, ordinal);
            if best.len() < 40 {
                best.push(entry);
            } else if best.peek().is_some_and(|worst| entry < *worst) {
                best.pop();
                best.push(entry);
            }
            ordinal += 1;
        });
        let mut selected = best.into_vec();
        selected.sort_unstable_by_key(|entry| entry.2);
        selected.into_iter().map(|entry| entry.1).collect()
    }
}

#[cfg(test)]
#[path = "incremental/tests.rs"]
mod tests;
