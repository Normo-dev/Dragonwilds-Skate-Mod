//! Optional bounded caller-owned geometry profile. No Session or game input.
#[path = "../src/compact_world.rs"]
mod compact_world;
#[path = "../src/exposed_edge_cache.rs"]
mod exposed_edge_cache;
#[path = "../src/exposed_edges.rs"]
mod exposed_edges;
#[path = "../src/exposed_query.rs"]
mod exposed_query;
use compact_world::{Bounds, CompactWorld};
use serde_json::json;
use std::{collections::BTreeMap, path::PathBuf, sync::Arc, time::Instant};
fn cell(p: [f64; 3]) -> ([i32; 3], Bounds) {
    let key = p.map(|v| (v / 3200.).floor() as i32);
    (
        key,
        Bounds {
            min: key.map(|v| v as f64 * 3200.),
            max: key.map(|v| (v as f64 + 1.) * 3200.),
        },
    )
}
// Sampling one cell cannot know whether short boundary components continue in
// a neighbor. Lower keeps eligible local networks; upper retains every network
// touching the cell boundary. This bounds, rather than invents, global counts.
fn filter_bounds(segments: &[exposed_edges::Segment], owner: Bounds) -> serde_json::Value {
    use exposed_edge_cache::{Manifest, Parameters, Snapshot};
    let mut snapshot = Snapshot {
        manifest: Manifest {
            magic: "DWE1".into(),
            schema: 1,
            complete: true,
            base_fingerprint: "b".repeat(64),
            scene_fingerprint: None,
            algorithm_sha256: "a".repeat(64),
            parameters: Parameters {
                cell_size_cm: 3200.,
                halo_cm: 100.,
                truck_distance_cm: 24.3,
                tolerance_cm: 1e-5,
            },
            cells: vec![],
            segments: segments.len(),
        },
        path: PathBuf::from("private-unused.json"),
        manifest_sha256: "c".repeat(64),
        cells: vec![Arc::new(segments.to_vec())],
        stats: json!({}),
    };
    let (_, lower) = exposed_edge_cache::rails(&snapshot).unwrap();
    let mut boundary = Vec::new();
    for s in segments {
        for p in [s.a, s.b] {
            if (0..3).any(|i| {
                let radius = (p[i].abs().max(1.) * f32::EPSILON as f64 * 2.).max(1e-5);
                (p[i] - owner.min[i]).abs() <= radius || (p[i] - owner.max[i]).abs() <= radius
            }) {
                boundary.push(exposed_edges::Segment {
                    a: p,
                    b: [p[0], p[1], p[2] + 100.],
                });
            }
        }
    }
    snapshot.manifest.segments += boundary.len();
    snapshot.cells.push(Arc::new(boundary));
    let (_, upper) = exposed_edge_cache::rails(&snapshot).unwrap();
    json!({"local":lower,"retained_segments_lower":lower["provider_segments"],"retained_segments_upper":lower["unique_segments"].as_u64().unwrap()-upper["excluded_segments"].as_u64().unwrap(),"boundary_rule":"upper retains every component touching the sampled cell boundary; not a measured whole-map count"})
}
#[test]
#[ignore = "read-only representative cells from external owned world; no game data bundled"]
fn representative_owned_world_cells() {
    let base_path = PathBuf::from(std::env::var("S3_EDGE_PROFILE_BASE").expect("base manifest"));
    let output = PathBuf::from(std::env::var("S3_EDGE_PROFILE_OUTPUT").expect("output report"));
    let started = Instant::now();
    let base = Arc::new(CompactWorld::load(&base_path).unwrap());
    let world = std::env::var("S3_EDGE_PROFILE_SCENE")
        .ok()
        .map(|p| CompactWorld::with_overlay(&base, std::path::Path::new(&p)).unwrap())
        .map(Arc::new)
        .unwrap_or(base);
    let loaded_ms = started.elapsed().as_millis();
    let occupied = exposed_edge_cache::occupied(&world).unwrap();
    let mut selected = BTreeMap::new();
    let mut uniform = Vec::new();
    for key in occupied.iter().step_by(occupied.len().div_ceil(64).max(1)) {
        let owner = cell(key.map(|v| v as f64 * 3200. + 1.)).1;
        let halo = Bounds {
            min: owner.min.map(|v| v - 100.),
            max: owner.max.map(|v| v + 100.),
        };
        uniform.push(
            json!({"key":key,"triangles":world.candidate_triangles(halo.min,halo.max).len()}),
        );
    }
    let mut census = Vec::new();
    for terrain in [true, false] {
        let groups: Vec<_> = world
            .groups()
            .iter()
            .filter(|g| (g.triangle_range.start < world.counts.terrain_triangles) == terrain)
            .collect();
        let mut best = None;
        for group in groups.iter().step_by(groups.len().div_ceil(64).max(1)) {
            let p = std::array::from_fn(|i| {
                if i == 2 {
                    group.bounds.max[i] - 0.01
                } else {
                    (group.bounds.min[i] + group.bounds.max[i]) * 0.5
                }
            });
            let (key, owner) = cell(p);
            let halo = Bounds {
                min: owner.min.map(|v| v - 100.),
                max: owner.max.map(|v| v + 100.),
            };
            let count = world.candidate_triangles(halo.min, halo.max).len();
            census.push(json!({"terrain":terrain,"key":key,"candidate_triangles":count}));
            if best.as_ref().is_none_or(|&(n, _, _)| count > n) {
                best = Some((count, key, owner));
            }
        }
        if let Some((_, key, owner)) = best {
            selected.insert(
                key,
                (
                    (if terrain {
                        "sampled_dense_terrain"
                    } else {
                        "sampled_dense_objects"
                    })
                    .to_owned(),
                    owner,
                ),
            );
        }
    }
    if let Ok(points) = std::env::var("S3_EDGE_PROFILE_POINTS") {
        let values: Vec<(String, [f64; 3])> = serde_json::from_str(&points).unwrap();
        for (label, p) in values {
            let (key, b) = cell(p);
            selected.insert(key, (label, b));
        }
    }
    let sample_mode = std::env::var("S3_EDGE_PROFILE_SAMPLE_N").ok();
    if let Some(count) = &sample_mode {
        let count: usize = count.parse().unwrap();
        assert!(count <= 512);
        selected.clear();
        let mut sample: Vec<_> = occupied.iter().copied().collect();
        sample.sort_by_key(|k| {
            let mut x = 0x4d595df4d0f33173u64;
            for v in k {
                x ^= *v as i64 as u64;
                x = x.wrapping_add(0x9e3779b97f4a7c15);
                x = (x ^ (x >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
                x = (x ^ (x >> 27)).wrapping_mul(0x94d049bb133111eb);
                x ^= x >> 31;
            }
            x
        });
        for (i, key) in sample.into_iter().take(count).enumerate() {
            let owner = cell(key.map(|v| v as f64 * 3200. + 1.)).1;
            selected.insert(key, (format!("distributed_{i}"), owner));
        }
    }
    let mut reports = Vec::new();
    for (key, (label, owner)) in selected {
        let timer = Instant::now();
        let halo = Bounds {
            min: owner.min.map(|v| v - 100.),
            max: owner.max.map(|v| v + 100.),
        };
        let triangles: Vec<_> = world
            .candidate_triangles(halo.min, halo.max)
            .into_iter()
            .map(|id| world.raw_triangle(id))
            .collect();
        let local = exposed_query::LocalQuery::new(&triangles, halo);
        let (mut calls, mut faces, mut timed_out, mut query_ns, mut local_hits, mut parity_ns) =
            (0usize, 0usize, false, 0u128, 0usize, 0u128);
        let groups:Vec<_>=world.candidate_groups(halo.min,halo.max).into_iter().map(|i|json!({"group":i,"triangle_start":world.groups()[i].triangle_range.start,"triangles":world.groups()[i].triangle_range.len(),"min":world.groups()[i].bounds.min,"max":world.groups()[i].bounds.max})).collect();
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            exposed_edges::derive(
                &triangles,
                owner,
                &mut |b| {
                    if timer.elapsed().as_secs() > 30 {
                        timed_out = true;
                        panic!("bounded profile exceeded30seconds");
                    }
                    let now = Instant::now();
                    let result = if let Some(value) = local.query(b) {
                        local_hits += 1;
                        value
                    } else {
                        world
                            .candidate_triangles(b.min, b.max)
                            .into_iter()
                            .map(|id| world.raw_triangle(id))
                            .collect()
                    };
                    query_ns += now.elapsed().as_nanos();
                    if calls < 64 {
                        let now = Instant::now();
                        let original: Vec<_> = world
                            .candidate_triangles(b.min, b.max)
                            .into_iter()
                            .map(|id| world.raw_triangle(id))
                            .collect();
                        assert_eq!(result, original, "Local world query differs");
                        parity_ns += now.elapsed().as_nanos();
                    }
                    calls += 1;
                    faces += result.len();
                    result
                },
                &exposed_edges::Config::default(),
            )
        }));
        let report = match result {
            Ok(Ok(patch)) => {
                let derivation_ms = timer.elapsed().as_millis();
                let filter = filter_bounds(&patch.segments, owner);
                if sample_mode.is_none() {
                    let detail = output.with_file_name(format!(
                        "cell-{}-{}-{}.private.json",
                        key[0], key[1], key[2]
                    ));
                    std::fs::write(detail,serde_json::to_vec(&json!({"key":key,"triangles":triangles,"groups":groups,"segments":patch.segments,"census":patch.census})).unwrap()).unwrap();
                }
                let mut lengths: Vec<_> = patch
                    .segments
                    .iter()
                    .map(|s| {
                        (0..3)
                            .map(|i| (s.b[i] - s.a[i]).powi(2))
                            .sum::<f64>()
                            .sqrt()
                    })
                    .collect();
                lengths.sort_by(f64::total_cmp);
                json!({"key":key,"label":label,"input_triangles":triangles.len(),"query_calls":calls,"queried_triangles":faces,"elapsed_ms":derivation_ms,"component_filter":filter,"segments":patch.segments.len(),"census":patch.census,"length_under1cm":lengths.iter().filter(|&&v|v<1.).count(),"length_under10cm":lengths.iter().filter(|&&v|v<10.).count(),"length_under50cm":lengths.iter().filter(|&&v|v<50.).count(),"query_ms":query_ns as f64/1e6,"parity_check_ms":parity_ns as f64/1e6,"local_query_hits":local_hits,"complete":true})
            }
            result => {
                json!({"key":key,"label":label,"input_triangles":triangles.len(),"query_calls":calls,"queried_triangles":faces,"elapsed_ms":timer.elapsed().as_millis(),"complete":false,"timed_out":timed_out,"error":format!("{result:?}")})
            }
        };
        eprintln!("EXPOSED_CELL_PROFILE {report}");
        reports.push(report);
        std::fs::write(&output,serde_json::to_vec_pretty(&json!({"source_fingerprint":world.source_fingerprint,"scene_fingerprint":world.scene_fingerprint,"world_load_ms":loaded_ms,"occupied_cells":occupied.len(),"uniform_cell_sample":uniform,"sampled_cells":census,"profiles":reports})).unwrap()).unwrap();
    }
}

#[test]
#[ignore = "bounded external cell batch measures four-worker extraction, without full preparation"]
fn four_worker_owned_cell_batch() {
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        mpsc,
    };
    let input = std::env::var("S3_EDGE_PARALLEL_INPUT").unwrap();
    let output = std::env::var("S3_EDGE_PARALLEL_OUTPUT").unwrap();
    let previous: serde_json::Value =
        serde_json::from_slice(&std::fs::read(input).unwrap()).unwrap();
    let keys: Vec<[i32; 3]> = previous["profiles"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| serde_json::from_value(v["key"].clone()).unwrap())
        .collect();
    assert!(keys.len() <= 512);
    let base = Arc::new(
        CompactWorld::load(std::path::Path::new(
            &std::env::var("S3_EDGE_PROFILE_BASE").unwrap(),
        ))
        .unwrap(),
    );
    let world = CompactWorld::with_overlay(
        &base,
        std::path::Path::new(&std::env::var("S3_EDGE_PROFILE_SCENE").unwrap()),
    )
    .unwrap();
    let p = exposed_edge_cache::Parameters {
        cell_size_cm: 3200.,
        halo_cm: 100.,
        truck_distance_cm: 24.3,
        tolerance_cm: 1e-5,
    };
    let mut runs = Vec::new();
    let mut expected = None;
    for workers in [1, 4] {
        let started = Instant::now();
        let cursor = AtomicUsize::new(0);
        let (tx, rx) = mpsc::sync_channel(4);
        let mut records = std::thread::scope(|scope| {
            for _ in 0..workers {
                let tx = tx.clone();
                let cursor = &cursor;
                let keys = &keys;
                let world = &world;
                let p = &p;
                scope.spawn(move || {
                    loop {
                        let i = cursor.fetch_add(1, Ordering::Relaxed);
                        let Some(&key) = keys.get(i) else { break };
                        tx.send((
                            key,
                            exposed_edge_cache::profile_cell(world, p, key).unwrap(),
                        ))
                        .unwrap();
                    }
                });
            }
            drop(tx);
            rx.into_iter().collect::<Vec<_>>()
        });
        records.sort_by_key(|r| r.0);
        if let Some(prior) = &expected {
            assert_eq!(prior, &records);
        } else {
            expected = Some(records.clone());
        }
        let elapsed = started.elapsed().as_millis();
        runs.push(json!({"workers":workers,"cells":keys.len(),"elapsed_ms":elapsed,"segments":records.iter().map(|r|r.1.1).sum::<usize>(),"exact_serial_payload_sha_parity":true}));
        eprintln!(
            "BOUNDED_PARALLEL_PROFILE workers={workers} cells={} elapsed_ms={elapsed}",
            keys.len()
        );
    }
    std::fs::write(output,serde_json::to_vec_pretty(&json!({"scope":"bounded cell extraction only; final I/O/provider time excluded","runs":runs})).unwrap()).unwrap();
}
