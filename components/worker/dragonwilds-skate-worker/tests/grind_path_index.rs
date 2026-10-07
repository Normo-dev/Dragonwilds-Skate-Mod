//! Small synthetic graphs; no owned game assets or native session required.
mod exposed_edges {
    #[derive(Clone, Copy)]
    pub struct Segment {
        pub a: [f64; 3],
        pub b: [f64; 3],
    }
}
#[path = "../src/grind_path_index.rs"]
mod grind_path_index;
use exposed_edges::Segment;
use grind_path_index::{CellInput, GrindPathIndex};
use std::collections::{HashMap, HashSet, VecDeque};
#[path = "support/grind_fixture_inputs.rs"]
mod fixture_inputs;
type Cells = Vec<([i32; 3], String, Vec<Segment>)>;
fn edge(a: [f64; 3], b: [f64; 3]) -> Segment {
    Segment { a, b }
}
fn straight(x: f64, n: usize) -> Vec<Segment> {
    (0..n)
        .map(|i| {
            edge(
                [x + i as f64 * 20., 0., 0.],
                [x + (i + 1) as f64 * 20., 0., 0.],
            )
        })
        .collect()
}
fn inputs(cells: &Cells) -> Vec<CellInput<'_>> {
    cells
        .iter()
        .map(|(key, identity, segments)| CellInput {
            key: *key,
            identity,
            segments,
        })
        .collect()
}
fn bits(rails: Vec<Vec<[f32; 3]>>) -> Vec<Vec<[u32; 3]>> {
    rails
        .into_iter()
        .map(|rail| rail.into_iter().map(|p| p.map(f32::to_bits)).collect())
        .collect()
}
// Frozen full graph algorithm, independent of the incremental implementation.
// Preserves source signed-zero output and its global greedy seed order.
fn reference(cells: &Cells) -> Vec<Vec<[f32; 3]>> {
    let point = |p: [f64; 3]| {
        [p[0] * 0.01, p[2] * 0.01, -p[1] * 0.01].map(|v| {
            let v = v as f32;
            if v == 0. { 0 } else { v.to_bits() }
        })
    };
    let mut seen = HashSet::new();
    let mut edges = Vec::new();
    let mut ordered: Vec<_> = cells.iter().collect();
    ordered.sort_by_key(|c| c.0);
    for (_, _, cell) in ordered {
        for s in cell {
            let a = point(s.a);
            let b = point(s.b);
            let key = if a < b { [a, b] } else { [b, a] };
            if a != b && seen.insert(key) {
                edges.push(if a < b { *s } else { edge(s.b, s.a) });
            }
        }
    }
    edges.sort_by_key(|s| [point(s.a), point(s.b)]);
    let mut nodes = HashMap::<[u32; 3], Vec<(usize, bool)>>::new();
    for (i, s) in edges.iter().enumerate() {
        nodes.entry(point(s.a)).or_default().push((i, false));
        nodes.entry(point(s.b)).or_default().push((i, true));
    }
    let mut visited = vec![false; edges.len()];
    let mut used = visited.clone();
    for seed in 0..edges.len() {
        if visited[seed] {
            continue;
        }
        let mut component = vec![seed];
        visited[seed] = true;
        let mut at = 0;
        let mut length = 0.;
        while at < component.len() {
            let s = &edges[component[at]];
            let a = point(s.a);
            let b = point(s.b);
            length += (0..3)
                .map(|i| {
                    let d = f64::from(f32::from_bits(b[i])) - f64::from(f32::from_bits(a[i]));
                    d * d
                })
                .sum::<f64>()
                .sqrt();
            for p in [a, b] {
                for &(n, _) in &nodes[&p] {
                    if !visited[n] {
                        visited[n] = true;
                        component.push(n)
                    }
                }
            }
            at += 1;
        }
        if length < 0.5 {
            for i in component {
                used[i] = true
            }
        }
    }
    let unit = |a: [f64; 3], b: [f64; 3]| {
        let a = point(a).map(|v| f64::from(f32::from_bits(v)));
        let b = point(b).map(|v| f64::from(f32::from_bits(v)));
        let d: [f64; 3] = std::array::from_fn(|i| b[i] - a[i]);
        let n = d.iter().map(|v| v * v).sum::<f64>().sqrt();
        d.map(|v| v / n)
    };
    let mut result = Vec::new();
    for i in 0..edges.len() {
        if used[i] {
            continue;
        }
        used[i] = true;
        let first = &edges[i];
        let mut line = VecDeque::from([first.a, first.b]);
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
                let e = &edges[next];
                let to = if end { e.a } else { e.b };
                let d = unit(at, to);
                if (0..3).map(|n| heading[n] * d[n]).sum::<f64>() < 0.82 {
                    break;
                }
                used[next] = true;
                if backwards {
                    line.push_front(to)
                } else {
                    line.push_back(to)
                }
                at = to;
                heading = d;
            }
        }
        result.push(
            line.into_iter()
                .map(|p| {
                    [
                        (p[0] * 0.01) as f32,
                        (p[2] * 0.01) as f32,
                        (-p[1] * 0.01) as f32,
                    ]
                })
                .collect(),
        );
    }
    result
}

#[test]
fn cold_output_matches_frozen_algorithm_for_branches_curves_and_signed_zero() {
    let mut segments = straight(0., 3);
    segments.extend([
        edge([100., 0., 0.], [130., 0., 0.]),
        edge([130., 0., 0.], [130., 30., 0.]),
        edge([200., 0., 0.], [220., 0., 0.]),
        edge([220., 0., 0.], [220., 20., 0.]),
        edge([0., 100., 0.], [50., 100., 0.]),
        edge([300., 0., 0.], [330., 0., 0.]),
        edge([330.01, 0., 0.], [360.01, 0., 0.]),
        edge([500., -0., -0.], [550., -0., -0.]),
        edge([0., 0., 0.], [0., 20., 0.]),
    ]);
    for i in 0..8 {
        let a = i as f64 * std::f64::consts::FRAC_PI_4;
        let b = (i + 1) as f64 * std::f64::consts::FRAC_PI_4;
        segments.push(edge(
            [700. + 10. * a.cos(), 10. * a.sin(), 0.],
            if i == 7 {
                [710., 0., 0.]
            } else {
                [700. + 10. * b.cos(), 10. * b.sin(), 0.]
            },
        ));
    }
    let cells = vec![
        ([0, 0, 0], "a".into(), segments[..5].to_vec()),
        ([1, 0, 0], "b".into(), segments[5..].to_vec()),
    ];
    let index = GrindPathIndex::build("base".into(), &inputs(&cells)).unwrap();
    assert_eq!(bits(index.rails()), bits(reference(&cells)));
}
#[test]
fn short_components_join_and_bridge_removal_splits_complete_component() {
    let mut cells = vec![
        ([0, 0, 0], "a".into(), straight(0., 2)),
        ([1, 0, 0], "b".into(), straight(60., 2)),
        ([2, 0, 0], "far".into(), straight(1000., 3)),
    ];
    let mut index = GrindPathIndex::build("base".into(), &inputs(&cells)).unwrap();
    assert_eq!(index.rail_count(), 1);
    cells.push((
        [3, 0, 0],
        "bridge".into(),
        vec![edge([40., 0., 0.], [60., 0., 0.])],
    ));
    let tx = index
        .begin("base", "joined".into(), &inputs(&cells))
        .unwrap();
    assert_eq!(tx.stats.input_segments_examined, 1);
    assert_eq!(tx.stats.rebuilt_component_edges, 5);
    assert_eq!(tx.delta.retired_owners.len(), 0);
    assert_eq!(tx.delta.rails.len(), 1);
    assert_eq!(bits(index.rails()), bits(reference(&cells)));
    index.commit(tx).unwrap();
    cells.pop();
    let tx = index
        .begin("joined", "split".into(), &inputs(&cells))
        .unwrap();
    assert_eq!(tx.stats.input_segments_examined, 0);
    assert_eq!(tx.stats.rebuilt_components, 2);
    assert_eq!(tx.stats.rebuilt_component_edges, 4);
    assert_eq!(tx.delta.retired_owners.len(), 1);
    assert!(tx.delta.rails.is_empty());
    index.commit(tx).unwrap();
    assert_eq!(bits(index.rails()), bits(reference(&cells)));
}
#[test]
fn shared_refs_transfer_without_rebuilding_and_rollback_preserves_owners() {
    let original = vec![
        ([0, 0, 0], "a".into(), straight(0., 3)),
        ([1, 0, 0], "b".into(), straight(0., 3)),
        ([2, 0, 0], "far".into(), straight(1000., 3)),
    ];
    let mut index = GrindPathIndex::build("base".into(), &inputs(&original)).unwrap();
    let before = bits(index.rails());
    let mut cells = original.clone();
    cells.remove(0);
    let tx = index.begin("base", "one".into(), &inputs(&cells)).unwrap();
    assert_eq!(tx.stats.rebuilt_component_edges, 0);
    assert!(tx.delta.rails.is_empty());
    assert!(tx.delta.retired_owners.is_empty());
    index.commit(tx).unwrap();
    cells[0].1 = "short".into();
    cells[0].2 = straight(0., 1);
    let tx = index
        .begin("one", "candidate".into(), &inputs(&cells))
        .unwrap();
    let retired = tx.delta.retired_owners.clone();
    assert_eq!(retired, vec![1]);
    assert!(index.begin("one", "stale".into(), &inputs(&cells)).is_err());
    index.rollback(tx).unwrap();
    assert_eq!(index.snapshot_identity(), "one");
    assert_eq!(bits(index.rails()), before);
    let tx = index
        .begin("one", "accepted".into(), &inputs(&cells))
        .unwrap();
    assert_eq!(tx.delta.retired_owners, retired);
    index.commit(tx).unwrap();
}
#[test]
fn invalid_input_and_stale_revision_leave_accepted_graph_intact() {
    let mut cells = vec![([0, 0, 0], "a".into(), straight(0., 3))];
    let mut index = GrindPathIndex::build("base".into(), &inputs(&cells)).unwrap();
    let before = bits(index.rails());
    assert!(index.begin("wrong", "new".into(), &inputs(&cells)).is_err());
    cells[0].1 = "bad".into();
    cells[0].2[0].a[0] = f64::NAN;
    assert!(index.begin("base", "new".into(), &inputs(&cells)).is_err());
    assert_eq!(index.snapshot_identity(), "base");
    assert_eq!(bits(index.rails()), before);
}
#[test]
fn successive_insertions_deletions_and_ref_changes_match_full_rebuild() {
    let mut cells: Cells = Vec::new();
    let mut index = GrindPathIndex::build("empty".into(), &inputs(&cells)).unwrap();
    let mut revision = "empty".to_string();
    for i in 0..80 {
        let key = [(i * 17 % 13) as i32, 0, 0];
        cells.retain(|cell| cell.0 != key);
        if i % 4 != 0 {
            let at = key[0] as f64 * 20.;
            let mut segment = vec![edge([at, 0., 0.], [at + 20., 0., 0.])];
            if i % 3 == 0 {
                segment.push(edge([at, 0., 0.], [at, 20., 0.]));
            }
            if i % 5 == 0 {
                segment.push(edge([0., 0., 0.], [20., 0., 0.]));
            }
            cells.push((key, format!("cell-{i}"), segment));
        }
        let next = format!("revision-{i}");
        let tx = index
            .begin(&revision, next.clone(), &inputs(&cells))
            .unwrap();
        assert_eq!(
            bits(index.rails()),
            bits(reference(&cells)),
            "iteration {i}"
        );
        if i % 7 == 0 {
            index.rollback(tx).unwrap();
            // Reapply the same replacement after rollback, checking stale
            // candidate owners cannot survive into accepted provider output.
            let tx = index
                .begin(&revision, next.clone(), &inputs(&cells))
                .unwrap();
            index.commit(tx).unwrap();
        } else {
            index.commit(tx).unwrap();
        }
        revision = next;
    }
}

struct Temp(std::path::PathBuf);
impl Temp {
    fn new() -> Self {
        use std::sync::atomic::{AtomicU64, Ordering};
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "dws-grind-index-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let path = self.0.canonicalize().unwrap();
        assert!(path.starts_with(std::env::temp_dir().canonicalize().unwrap()));
        assert!(
            path.file_name()
                .unwrap()
                .to_str()
                .unwrap()
                .starts_with("dws-grind-index-")
        );
        std::fs::remove_dir_all(path).unwrap();
    }
}
fn fixture_files(temp: &Temp) -> std::path::PathBuf {
    let source = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/grind-v1");
    let folder = temp.0.join("original-fixtures");
    std::fs::create_dir(&folder).unwrap();
    for entry in std::fs::read_dir(source).unwrap() {
        let path = entry.unwrap().path();
        if path.extension().is_some_and(|ext| ext == "hex") {
            let hex: String = std::fs::read_to_string(&path)
                .unwrap()
                .split_whitespace()
                .collect();
            assert_eq!(hex.len() % 2, 0);
            let bytes: Vec<u8> = (0..hex.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
                .collect();
            std::fs::write(folder.join(path.file_stem().unwrap()), bytes).unwrap();
        } else if path.extension().is_some_and(|ext| ext == "json") {
            std::fs::copy(&path, folder.join(path.file_name().unwrap())).unwrap();
        }
    }
    folder
}
#[test]
fn persistent_base_and_small_delta_replay_preserve_topology_owners_and_native_bits() {
    let temp = Temp::new();
    let algorithm = grind_path_index::algorithm(&"f".repeat(64));
    let mut cells: Cells = (0..100)
        .map(|i| {
            (
                [i, 0, 0],
                format!("cell-{i}"),
                straight(i as f64 * 1000., 3),
            )
        })
        .collect();
    let mut index = GrindPathIndex::build("a".repeat(64), &inputs(&cells)).unwrap();
    let base = temp.0.join("base.dgi1");
    index.store(&base, &algorithm).unwrap();
    let restored = GrindPathIndex::load(&base, &"a".repeat(64), &algorithm).unwrap();
    assert_eq!(bits(restored.rails()), bits(index.rails()));
    assert_eq!(restored.stats()["provider_segments"], 300);
    cells[50].1 = "changed".into();
    cells[50].2 = straight(50000., 2);
    let tx = index
        .begin(&"a".repeat(64), "b".repeat(64), &inputs(&cells))
        .unwrap();
    let delta = temp.0.join("delta.dgi1");
    index.store_delta(&delta, &base, &tx, &algorithm).unwrap();
    index.commit(tx).unwrap();
    assert!(
        std::fs::metadata(&delta).unwrap().len() < std::fs::metadata(&base).unwrap().len() / 20
    );
    let mut restored = GrindPathIndex::load(&delta, &"b".repeat(64), &algorithm).unwrap();
    assert_eq!(bits(restored.rails()), bits(index.rails()));
    cells[51].1 = "next".into();
    cells[51].2 = straight(51000., 2);
    let tx = index
        .begin(&"b".repeat(64), "c".repeat(64), &inputs(&cells))
        .unwrap();
    let other = restored
        .begin(&"b".repeat(64), "c".repeat(64), &inputs(&cells))
        .unwrap();
    assert_eq!(tx.delta.retired_owners, other.delta.retired_owners);
    assert_eq!(tx.delta.rails, other.delta.rails);
    restored.rollback(other).unwrap();
    index.rollback(tx).unwrap();
    // Fresh provider construction assigns new initial global ordinals.
    restored.rebind_initial_owners().unwrap();
    let mut fresh = GrindPathIndex::build(
        "b".repeat(64),
        &inputs(&{
            let mut previous = cells.clone();
            previous[51].1 = "cell-51".into();
            previous[51].2 = straight(51000., 3);
            previous
        }),
    )
    .unwrap();
    let actual = restored
        .begin(&"b".repeat(64), "c".repeat(64), &inputs(&cells))
        .unwrap();
    let expected = fresh
        .begin(&"b".repeat(64), "c".repeat(64), &inputs(&cells))
        .unwrap();
    assert_eq!(actual.delta.retired_owners, expected.delta.retired_owners);
    let rebound_delta = temp.0.join("rebound.dgi1");
    restored
        .store_delta(&rebound_delta, &delta, &actual, &algorithm)
        .unwrap();
    restored.commit(actual).unwrap();
    fresh.rollback(expected).unwrap();
    let mut replay = GrindPathIndex::load(&rebound_delta, &"c".repeat(64), &algorithm).unwrap();
    cells[52].1 = "after-rebind".into();
    cells[52].2 = straight(52000., 2);
    let actual = restored
        .begin(&"c".repeat(64), "d".repeat(64), &inputs(&cells))
        .unwrap();
    let expected = replay
        .begin(&"c".repeat(64), "d".repeat(64), &inputs(&cells))
        .unwrap();
    assert_eq!(actual.delta.retired_owners, expected.delta.retired_owners);
    assert_eq!(actual.delta.rails, expected.delta.rails);
    restored.rollback(actual).unwrap();
    replay.rollback(expected).unwrap();
}
#[test]
fn persistence_rejects_wrong_snapshot_algorithm_corruption_and_missing_ancestor() {
    let temp = Temp::new();
    let algorithm = grind_path_index::algorithm(&"f".repeat(64));
    let cells = vec![([0, 0, 0], "a".into(), straight(0., 3))];
    let mut index = GrindPathIndex::build("a".repeat(64), &inputs(&cells)).unwrap();
    let path = temp.0.join("base.dgi1");
    index.store(&path, &algorithm).unwrap();
    assert!(GrindPathIndex::load(&path, &"b".repeat(64), &algorithm).is_err());
    assert!(GrindPathIndex::load(&path, &"a".repeat(64), &"f".repeat(64)).is_err());
    let tx = index
        .begin(&"a".repeat(64), "b".repeat(64), &inputs(&cells))
        .unwrap();
    let delta = temp.0.join("delta.dgi1");
    index.store_delta(&delta, &path, &tx, &algorithm).unwrap();
    index.commit(tx).unwrap();
    let mut bytes = std::fs::read(&path).unwrap();
    bytes[140] ^= 1;
    std::fs::write(&path, bytes).unwrap();
    assert!(GrindPathIndex::load(&path, &"a".repeat(64), &algorithm).is_err());
    assert!(GrindPathIndex::load(&delta, &"b".repeat(64), &algorithm).is_err());
    std::fs::remove_file(path).unwrap();
    assert!(GrindPathIndex::load(&delta, &"b".repeat(64), &algorithm).is_err());
}

#[test]
fn proposed_compaction_preserves_candidate_owners_and_rollback_restores_depth() {
    let temp = Temp::new();
    let algorithm = grind_path_index::algorithm(&"f".repeat(64));
    let mut cells = vec![([0, 0, 0], "a".into(), straight(0., 3))];
    let mut index = GrindPathIndex::build("a".repeat(64), &inputs(&cells)).unwrap();
    let before = bits(index.rails());
    let base = temp.0.join("base.dgi1");
    index.store(&base, &algorithm).unwrap();
    assert_eq!(index.delta_depth(), 0);
    cells[0].1 = "changed".into();
    cells[0].2 = straight(0., 4);
    let tx = index
        .begin(&"a".repeat(64), "b".repeat(64), &inputs(&cells))
        .unwrap();
    assert_eq!(index.delta_depth(), 1);
    let compact = temp.0.join("compact.dgi1");
    assert!(index.store(&compact, &algorithm).is_err());
    index.store_proposed(&compact, &algorithm, &tx).unwrap();
    assert_eq!(index.delta_depth(), 0);
    let mut restored = GrindPathIndex::load(&compact, &"b".repeat(64), &algorithm).unwrap();
    assert_eq!(restored.delta_depth(), 0);
    assert_eq!(bits(restored.rails()), bits(index.rails()));
    index.rollback(tx).unwrap();
    assert_eq!(index.delta_depth(), 0);
    assert_eq!(bits(index.rails()), before);
    let tx = index
        .begin(&"a".repeat(64), "b".repeat(64), &inputs(&cells))
        .unwrap();
    let delta = temp.0.join("delta.dgi1");
    index.store_delta(&delta, &base, &tx, &algorithm).unwrap();
    index.commit(tx).unwrap();
    assert_eq!(index.delta_depth(), 1);
    let loaded = GrindPathIndex::load(&delta, &"b".repeat(64), &algorithm).unwrap();
    assert_eq!(loaded.delta_depth(), 1);
    cells[0].1 = "short".into();
    cells[0].2 = straight(0., 2);
    let expected = restored
        .begin(&"b".repeat(64), "c".repeat(64), &inputs(&cells))
        .unwrap();
    let actual = index
        .begin(&"b".repeat(64), "c".repeat(64), &inputs(&cells))
        .unwrap();
    assert_eq!(actual.delta.retired_owners, expected.delta.retired_owners);
    index.rollback(actual).unwrap();
    assert_eq!(index.delta_depth(), 1);
    restored.rollback(expected).unwrap();
    // Neither checkpoint nor compaction may replace a previously published
    // immutable graph, even when a scene fingerprint is revisited.
    assert!(index.store(&base, &algorithm).is_err());
    assert_eq!(
        bits(
            GrindPathIndex::load(&base, &"a".repeat(64), &algorithm)
                .unwrap()
                .rails()
        ),
        before
    );
}

#[test]
fn compact_storage_preserves_original_base_delta_and_compaction_bytes() {
    let temp = Temp::new();
    let fixtures = fixture_files(&temp);
    let algorithm = "f".repeat(64);
    let cells = fixture_inputs::cells();
    let mut index = GrindPathIndex::build("a".repeat(64), &inputs(&cells)).unwrap();
    let base = temp.0.join("legacy-base.dgi1");
    index.store(&base, &algorithm).unwrap();
    assert_eq!(
        std::fs::read(&base).unwrap(),
        std::fs::read(fixtures.join("legacy-base.dgi1")).unwrap()
    );
    let loaded = GrindPathIndex::load(&base, &"a".repeat(64), &algorithm).unwrap();
    let roundtrip = temp.0.join("roundtrip.dgi1");
    loaded.store(&roundtrip, &algorithm).unwrap();
    assert_eq!(
        std::fs::read(&base).unwrap(),
        std::fs::read(roundtrip).unwrap()
    );
    let changed = fixture_inputs::changed(cells);
    let tx = index
        .begin(&"a".repeat(64), "b".repeat(64), &inputs(&changed))
        .unwrap();
    let delta = temp.0.join("legacy-delta.dgi1");
    index.store_delta(&delta, &base, &tx, &algorithm).unwrap();
    assert_eq!(
        std::fs::read(&delta).unwrap(),
        std::fs::read(fixtures.join("legacy-delta.dgi1")).unwrap()
    );
    let compact = temp.0.join("legacy-compacted.dgi1");
    index.store_proposed(&compact, &algorithm, &tx).unwrap();
    let compact_loaded = GrindPathIndex::load(&compact, &"b".repeat(64), &algorithm).unwrap();
    let original_compact = GrindPathIndex::load(
        &fixtures.join("legacy-compacted.dgi1"),
        &"b".repeat(64),
        &algorithm,
    )
    .unwrap();
    assert_eq!(bits(compact_loaded.rails()), bits(original_compact.rails()));
    let compact_roundtrip = temp.0.join("compact-roundtrip.dgi1");
    compact_loaded
        .store(&compact_roundtrip, &algorithm)
        .unwrap();
    assert_eq!(
        std::fs::read(&compact).unwrap(),
        std::fs::read(&compact_roundtrip).unwrap()
    );
    index.rollback(tx).unwrap();
    assert_eq!(bits(index.rails()), bits(loaded.rails()));
    let replay = GrindPathIndex::load(&delta, &"b".repeat(64), &algorithm).unwrap();
    assert_eq!(bits(replay.rails()), bits(reference(&changed)));

    // Freed slots depend on the original randomized differences-map order.
    // Compare the original full candidate bytes on an additions-only update,
    // where slots are deterministic; deletion/reuse is checked above and by
    // the separate owner/rollback stress test.
    let extended = fixture_inputs::extended(fixture_inputs::cells());
    let mut index =
        GrindPathIndex::build("a".repeat(64), &inputs(&fixture_inputs::cells())).unwrap();
    let tx = index
        .begin(&"a".repeat(64), "b".repeat(64), &inputs(&extended))
        .unwrap();
    let added_compact = temp.0.join("legacy-added-compacted.dgi1");
    index
        .store_proposed(&added_compact, &algorithm, &tx)
        .unwrap();
    assert_eq!(
        std::fs::read(&added_compact).unwrap(),
        std::fs::read(fixtures.join("legacy-added-compacted.dgi1")).unwrap()
    );
}

#[test]
fn exported_native_singletons_roundtrip_original_bytes_and_decode_rebind_delta() {
    let temp = Temp::new();
    let fixtures = fixture_files(&temp);
    let metadata: serde_json::Value =
        serde_json::from_slice(&std::fs::read(fixtures.join("records.json")).unwrap()).unwrap();
    for (name, letter) in [("initial", 'c'), ("next", 'd')] {
        let identity = letter.to_string().repeat(64);
        let pin = metadata[name]["algorithm_sha256"].as_str().unwrap();
        let source = fixtures.join(format!("{identity}.dgi1"));
        let index = GrindPathIndex::load(&source, &identity, pin).unwrap();
        assert_eq!(index.rail_count(), 2);
        assert_eq!(index.stats()["provider_segments"], 2);
        index.assert_storage_integrity();
        let roundtrip = temp.0.join(format!("{name}.dgi1"));
        index.store(&roundtrip, pin).unwrap();
        let loaded = GrindPathIndex::load(&roundtrip, &identity, pin).unwrap();
        assert_eq!(bits(index.rails()), bits(loaded.rails()));
        if name == "initial" {
            assert_eq!(
                std::fs::read(source).unwrap(),
                std::fs::read(roundtrip).unwrap()
            );
        }
    }
}

#[test]
fn degree_changes_slot_reuse_and_rollback_preserve_all_untouched_owners() {
    let original = fixture_inputs::cells();
    let mut index = GrindPathIndex::build("base".into(), &inputs(&original)).unwrap();
    let original_bits = bits(index.rails());
    for count in (0..=8).rev() {
        let mut cells = original.clone();
        cells[1].1 = format!("degree-{count}");
        cells[1].2.truncate(count);
        // New isolated components reuse the deleted spoke slots.
        for n in 0..8 - count {
            cells.push((
                [100 + n as i32, 0, 0],
                format!("reused-{n}"),
                vec![edge(
                    [10000. + n as f64 * 200., -0., -0.],
                    [10100. + n as f64 * 200., -0., -0.],
                )],
            ));
        }
        let tx = index
            .begin("base", "candidate".into(), &inputs(&cells))
            .unwrap();
        assert_eq!(
            bits(index.rails()),
            bits(reference(&cells)),
            "degree {count}"
        );
        let retired = tx.delta.retired_owners.clone();
        let added = tx.delta.rails.clone();
        index.rollback(tx).unwrap();
        assert_eq!(bits(index.rails()), original_bits);
        let reapplied = index
            .begin("base", "candidate".into(), &inputs(&cells))
            .unwrap();
        assert_eq!(reapplied.delta.retired_owners, retired);
        assert_eq!(reapplied.delta.rails, added);
        index.rollback(reapplied).unwrap();
    }
}

#[test]
fn independent_replays_preserve_owners_slots_compaction_and_rebind_after_many_edits() {
    let temp = Temp::new();
    let algorithm = "f".repeat(64);
    let mut cells = fixture_inputs::cells();
    let mut identity = format!("{:064x}", 0);
    let mut index = GrindPathIndex::build(identity.clone(), &inputs(&cells)).unwrap();
    let mut parent = temp.0.join("step-0.dgi1");
    index.store(&parent, &algorithm).unwrap();
    for edit in 1..=130 {
        if edit == 40 || edit == 129 {
            index.rebind_initial_owners().unwrap();
        }
        cells = fixture_inputs::cells();
        cells[1].1 = format!("branches-{edit}");
        cells[1].2.truncate(edit % 9);
        cells[3].1 = format!("single-{edit}");
        cells[3].2 = vec![edge(
            [6000. + edit as f64 * 100., -0., -0.],
            [6100. + edit as f64 * 100., -0., -0.],
        )];
        for n in 0..edit % 4 {
            let x = 30000. + edit as f64 * 1000. + n as f64 * 200.;
            cells.push((
                [100 + n as i32, 0, 0],
                format!("new-{edit}-{n}"),
                vec![edge([x, -0., -0.], [x + 100., -0., -0.])],
            ));
        }
        let next = format!("{edit:064x}");
        if edit % 7 == 0 {
            let before = temp.0.join(format!("before-rollback-{edit}.dgi1"));
            index.store(&before, &algorithm).unwrap();
            let tx = index
                .begin(&identity, next.clone(), &inputs(&cells))
                .unwrap();
            index.rollback(tx).unwrap();
            let after = temp.0.join(format!("after-rollback-{edit}.dgi1"));
            index.store(&after, &algorithm).unwrap();
            let before = GrindPathIndex::load(&before, &identity, &algorithm).unwrap();
            let after = GrindPathIndex::load(&after, &identity, &algorithm).unwrap();
            assert_eq!(
                bits(before.rails()),
                bits(after.rails()),
                "accepted topology after rejected edit {edit}"
            );
            for key in [
                "unique_segments",
                "duplicate_segments",
                "provider_segments",
                "stitched_joints",
                "retained_components",
                "excluded_components",
                "excluded_segments",
                "excluded_arclength_cm",
            ] {
                assert_eq!(
                    before.stats()[key],
                    after.stats()[key],
                    "accepted census {key} after rejected edit {edit}"
                );
            }
            index.assert_storage_integrity();
        }
        let tx = index
            .begin(&identity, next.clone(), &inputs(&cells))
            .unwrap();
        let path = temp.0.join(format!("step-{edit}.dgi1"));
        if index.delta_depth() >= 128 {
            assert_eq!(edit, 128);
            index.store_proposed(&path, &algorithm, &tx).unwrap();
        } else {
            index.store_delta(&path, &parent, &tx, &algorithm).unwrap();
        }
        index.commit(tx).unwrap();
        identity = next;
        parent = path;
        if edit == 127 {
            let path = parent.clone();
            let snapshot = identity.clone();
            let algorithm = algorithm.clone();
            // The Windows worker executable reserves a 1 MiB main stack.
            std::thread::Builder::new()
                .stack_size(1024 * 1024)
                .spawn(move || GrindPathIndex::load(&path, &snapshot, &algorithm).map(|_| ()))
                .unwrap()
                .join()
                .unwrap()
                .unwrap();
        }
        let mut replay = GrindPathIndex::load(&parent, &identity, &algorithm).unwrap();
        assert_eq!(
            bits(index.rails()),
            bits(reference(&cells)),
            "native output at edit {edit}"
        );
        assert_eq!(bits(replay.rails()), bits(index.rails()));
        let live_file = temp.0.join(format!("live-{edit}.dgi1"));
        let replay_file = temp.0.join(format!("replay-{edit}.dgi1"));
        index.store(&live_file, &algorithm).unwrap();
        replay.store(&replay_file, &algorithm).unwrap();
        // A rejected candidate can leave different vacant slot layouts. Both
        // full states must remain loadable, with identical public semantics.
        let live_roundtrip = GrindPathIndex::load(&live_file, &identity, &algorithm).unwrap();
        let replay_roundtrip = GrindPathIndex::load(&replay_file, &identity, &algorithm).unwrap();
        assert_eq!(bits(live_roundtrip.rails()), bits(replay_roundtrip.rails()));
        for key in [
            "unique_segments",
            "duplicate_segments",
            "provider_segments",
            "stitched_joints",
            "retained_components",
            "excluded_components",
            "excluded_segments",
            "excluded_arclength_cm",
        ] {
            assert_eq!(
                index.stats()[key],
                replay.stats()[key],
                "replay census {key} at edit {edit}"
            );
        }
        index.assert_storage_integrity();
        replay.assert_storage_integrity();
        let mut future = cells.clone();
        future[0].1 = format!("probe-{edit}");
        future[0].2 = straight(0., 6);
        let probe = format!("{:064x}", 1000 + edit);
        let live_tx = index
            .begin(&identity, probe.clone(), &inputs(&future))
            .unwrap();
        let replay_tx = replay.begin(&identity, probe, &inputs(&future)).unwrap();
        assert_eq!(
            live_tx.delta.retired_owners, replay_tx.delta.retired_owners,
            "retired owners at edit {edit}"
        );
        assert_eq!(
            live_tx
                .delta
                .rails
                .iter()
                .map(|(id, _)| *id)
                .collect::<Vec<_>>(),
            replay_tx
                .delta
                .rails
                .iter()
                .map(|(id, _)| *id)
                .collect::<Vec<_>>()
        );
        assert_eq!(
            bits(
                live_tx
                    .delta
                    .rails
                    .iter()
                    .map(|(_, points)| points.clone())
                    .collect()
            ),
            bits(
                replay_tx
                    .delta
                    .rails
                    .iter()
                    .map(|(_, points)| points.clone())
                    .collect()
            )
        );
        index.rollback(live_tx).unwrap();
        replay.rollback(replay_tx).unwrap();
        index.assert_storage_integrity();
        replay.assert_storage_integrity();
    }
    assert_eq!(index.delta_depth(), 2);
}
