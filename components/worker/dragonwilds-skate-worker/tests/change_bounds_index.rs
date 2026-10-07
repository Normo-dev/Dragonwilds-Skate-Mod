#![allow(dead_code)]
#[path = "../src/change_bounds_index.rs"]
mod change_bounds_index;
#[path = "../src/compact_world.rs"]
mod compact_world;
#[path = "../src/exposed_edge_cache.rs"]
mod exposed_edge_cache;
#[path = "../src/exposed_edges.rs"]
mod exposed_edges;
#[path = "../src/exposed_query.rs"]
mod exposed_query;
use change_bounds_index::ChangeBoundsIndex;
use compact_world::{Bounds, CompactWorld};
use std::{hint::black_box, sync::Arc, time::Instant};

fn overlap(a: Bounds, b: Bounds) -> bool {
    (0..3).all(|i| a.min[i] <= b.max[i] && b.min[i] <= a.max[i])
}
fn exact(changes: &[Bounds], query: Bounds) -> bool {
    changes.iter().any(|&b| overlap(b, query))
}
fn box_(min: [f64; 3], max: [f64; 3]) -> Bounds {
    Bounds { min, max }
}
fn compare(changes: &[Bounds], queries: &[Bounds]) {
    let index = ChangeBoundsIndex::new(changes).unwrap();
    for &query in queries {
        assert_eq!(index.any_overlap(query), exact(changes, query), "{query:?}");
    }
}

#[test]
fn empty_small_and_large_inputs_preserve_inclusive_touching_and_signed_zero() {
    let special = [box_([-0.0, 0., -0.0], [0., 0., 0.]), box_([1.; 3], [2.; 3])];
    let next = f64::from_bits(2f64.to_bits() + 1);
    let queries = [
        box_([0.; 3], [0.; 3]),
        box_([2.; 3], [3.; 3]),
        box_([next; 3], [3.; 3]),
        box_([2., 0., 0.], [2., 1., 1.]),
        box_([-1.; 3], [1.; 3]),
    ];
    compare(&[], &queries);
    compare(&special, &queries);
    let mut many: Vec<_> = (0..80)
        .map(|i| {
            box_(
                [f64::from(i) * 10. + 100.; 3],
                [f64::from(i) * 10. + 101.; 3],
            )
        })
        .collect();
    many.extend(special);
    compare(&many, &queries);
}

#[test]
fn broad_node_union_never_proves_overlap_between_exact_leaves() {
    let changes: Vec<_> = (0..100)
        .map(|i| box_([f64::from(i) * 100.; 3], [f64::from(i) * 100. + 1.; 3]))
        .collect();
    let index = ChangeBoundsIndex::new(&changes).unwrap();
    assert!(!index.any_overlap(box_([50.; 3], [51.; 3])));
    assert!(index.any_overlap(box_([99.; 3], [100.; 3])));
}

#[test]
fn finite_extreme_large_volume_and_subnormal_boxes_match_linear_predicate() {
    let mut changes: Vec<_> = (0..70)
        .map(|i| box_([f64::from(i) * 1e300; 3], [f64::from(i) * 1e300 + 1e290; 3]))
        .collect();
    changes.extend([
        box_([-f64::MAX; 3], [f64::MAX; 3]),
        box_([f64::from_bits(1); 3], [f64::MIN_POSITIVE; 3]),
        box_([-f64::MAX; 3], [-f64::MAX; 3]),
    ]);
    compare(
        &changes,
        &[
            box_([-f64::MAX; 3], [-f64::MAX; 3]),
            box_([f64::MAX; 3], [f64::MAX; 3]),
            box_([0.; 3], [0.; 3]),
            box_([f64::from_bits(1); 3], [f64::from_bits(1); 3]),
        ],
    );
    changes.remove(70);
    compare(
        &changes,
        &[
            box_([f64::MAX; 3], [f64::MAX; 3]),
            box_([-1.; 3], [0.; 3]),
            box_([1e-300; 3], [2e-300; 3]),
        ],
    );
}

#[test]
fn invalid_change_bounds_are_rejected_on_both_execution_paths() {
    for bad in [
        box_([f64::NAN, 0., 0.], [1.; 3]),
        box_([0.; 3], [f64::INFINITY, 1., 1.]),
        box_([2.; 3], [1.; 3]),
    ] {
        assert!(ChangeBoundsIndex::new(&[bad]).is_err());
        let mut many = vec![box_([0.; 3], [1.; 3]); 40];
        many.push(bad);
        assert!(ChangeBoundsIndex::new(&many).is_err());
    }
}

#[test]
fn randomized_multi_dependency_dirty_decisions_match_original_change_major_loop() {
    let mut state = 0x620d19a7806b4c53u64;
    let mut next = || {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        state
    };
    for n in [0, 1, 8, 32, 33, 129, 1024] {
        let mut changes = Vec::new();
        for _ in 0..n {
            let min = std::array::from_fn(|_| (next() % 200_000) as f64 - 100_000.);
            let max = std::array::from_fn(|i| min[i] + (next() % 10_000) as f64);
            changes.push(box_(min, max));
        }
        let index = ChangeBoundsIndex::new(&changes).unwrap();
        for row in 0..2500 {
            let mut dependencies = Vec::new();
            for _ in 0..(row % 5 + 1) {
                let min = std::array::from_fn(|_| (next() % 200_000) as f64 - 100_000.);
                let max = std::array::from_fn(|i| min[i] + (next() % 10_000) as f64);
                dependencies.push(box_(min, max));
            }
            if !changes.is_empty() && row % 13 == 0 {
                dependencies.push(changes[row % changes.len()]);
            }
            let original = changes
                .iter()
                .any(|&b| dependencies.iter().any(|&r| overlap(r, b)));
            let indexed = dependencies.iter().any(|&r| index.any_overlap(r));
            assert_eq!(indexed, original, "n={n} row={row}");
        }
    }
}

#[test]
#[ignore = "Reads only the caller's copied private world; no Session, cache writes, or physics"]
fn actual_copied_scene_occupancy_and_dirty_selection_benchmark() {
    use serde::Deserialize;
    #[derive(Deserialize)]
    struct Region {
        min: [f64; 3],
        max: [f64; 3],
    }
    #[derive(Deserialize)]
    struct Cell {
        dependencies: Vec<Region>,
    }
    #[derive(Deserialize)]
    struct Dwe {
        base_fingerprint: String,
        scene_fingerprint: Option<String>,
        complete: bool,
        cells: Vec<Cell>,
    }
    let base_path = std::env::var("SKATE_DIRTY_BOUNDS_BASE").expect("Copied base path required");
    let scene_path =
        std::env::var("SKATE_DIRTY_BOUNDS_SCENE").expect("Copied overlay path required");
    let dwe_path = std::env::var("SKATE_DIRTY_BOUNDS_DWE").expect("Copied base DWE required");
    eprintln!("DIRTY_BOUNDS_BENCH phase=loading_copied_geometry");
    let base = Arc::new(CompactWorld::load(std::path::Path::new(&base_path)).unwrap());
    let scene = CompactWorld::with_overlay(&base, std::path::Path::new(&scene_path)).unwrap();
    let previous = match std::env::var("SKATE_DIRTY_BOUNDS_PRIOR_SCENE") {
        Ok(path) => Arc::new(CompactWorld::with_overlay(&base, std::path::Path::new(&path)).unwrap()),
        Err(_) => base.clone(),
    };
    let changes = scene.difference_bounds(&previous).unwrap();
    let dwe: Dwe = serde_json::from_slice(&std::fs::read(&dwe_path).unwrap()).unwrap();
    assert!(dwe.complete && dwe.scene_fingerprint == previous.scene_fingerprint);
    assert_eq!(dwe.base_fingerprint, base.source_fingerprint);
    eprintln!(
        "DIRTY_BOUNDS_BENCH phase=occupancy changes={}",
        changes.len()
    );
    let started = Instant::now();
    let keys = exposed_edge_cache::occupied(&scene).unwrap();
    let occupied_ms = started.elapsed().as_secs_f64() * 1000.;
    let started = Instant::now();
    let index = ChangeBoundsIndex::new(black_box(&changes)).unwrap();
    let build_ms = started.elapsed().as_secs_f64() * 1000.;
    let dependencies: Vec<Vec<Bounds>> = dwe
        .cells
        .iter()
        .map(|c| c.dependencies.iter().map(|r| box_(r.min, r.max)).collect())
        .collect();
    let started = Instant::now();
    let decisions: Vec<_> = dependencies
        .iter()
        .map(|rs| rs.iter().any(|&r| index.any_overlap(r)))
        .collect();
    let indexed_ms = started.elapsed().as_secs_f64() * 1000.;
    let dirty = decisions.iter().filter(|&&b| b).count();
    let sample: Vec<_> = (0..4096.min(dependencies.len()))
        .map(|i| i * dependencies.len() / 4096.min(dependencies.len()))
        .collect();
    let started = Instant::now();
    for &i in &sample {
        let original = changes
            .iter()
            .any(|&b| dependencies[i].iter().any(|&r| overlap(r, b)));
        assert_eq!(original, decisions[i]);
        black_box(original);
    }
    let linear_sample_ms = started.elapsed().as_secs_f64() * 1000.;
    eprintln!(
        "DIRTY_BOUNDS_BENCH {}",
        serde_json::json!({"changes":changes.len(),"previous_cells":dependencies.len(),
        "current_occupied_cells":keys.len(),"occupied_ms":occupied_ms,"index_build_ms":build_ms,"indexed_all_cells_ms":indexed_ms,
        "dirty_cells":dirty,"linear_sample_cells":sample.len(),"linear_sample_ms":linear_sample_ms,
        "linear_full_extrapolated_ms":linear_sample_ms*dependencies.len()as f64/sample.len()as f64,
        "minimum_original_overlap_tests_for_clean_cells":(dependencies.len()-dirty)as u64*changes.len()as u64})
    );
}
