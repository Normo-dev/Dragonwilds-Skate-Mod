//! Optional caller-owned dense geometry contact audit; no assets bundled.
//! The real capped provider, truck rectangles and retained 50-50 admission run
//! at every station. A rail count alone cannot establish grind continuity.
#[path = "../src/compact_world.rs"]
mod compact_world;
#[path = "../src/exposed_edge_cache.rs"]
mod exposed_edge_cache;
#[path = "../src/exposed_edges.rs"]
mod exposed_edges;
#[path = "../src/exposed_query.rs"]
mod exposed_query;
#[path = "../../skate3-mashup/skate/crates/skate-host/src/grind_world/octree.rs"]
mod octree;
#[path = "../../skate3-mashup/skate/crates/skate-host/src/grind_world/provider.rs"]
mod provider;
#[path = "../../skate3-mashup/skate/crates/skate-host/src/grind_world/spline.rs"]
mod spline;

use compact_world::Bounds;
use exposed_edge_cache::{Manifest, Parameters, Snapshot};
use exposed_edges::{Config, Triangle};
use provider::StaticProvider;
use serde_json::{Value, json};
use skate_core::{
    physics::grind_contact::{
        self,
        admission::{Admission, EntryKind},
        investigator,
    },
    point_graph::PointGraph,
};
use std::{path::PathBuf, sync::Arc};
type V = [f32; 4];
const TRUCK: f32 = 0.243;
const WHEEL: f32 = 0.09;
const GRAPH: PointGraph<4> = PointGraph {
    x: [0., 0.3, 0.7, 1.],
    y: [1.; 4],
};

fn cube(lo: [f64; 3], hi: [f64; 3]) -> Vec<Triangle> {
    let points: Vec<[f64; 3]> = (0..8)
        .map(|n| std::array::from_fn(|a| if n & (1 << a) == 0 { lo[a] } else { hi[a] }))
        .collect();
    [
        [0, 4, 6],
        [0, 6, 2],
        [1, 3, 7],
        [1, 7, 5],
        [0, 1, 5],
        [0, 5, 4],
        [2, 6, 7],
        [2, 7, 3],
        [0, 2, 3],
        [0, 3, 1],
        [4, 5, 7],
        [4, 7, 6],
    ]
    .into_iter()
    .map(|t| t.map(|i| points[i]))
    .collect()
}
fn transform(p: [f64; 3], yaw: f64, pitch: f64, offset: [f64; 3]) -> [f64; 3] {
    let (c, s) = (yaw.cos(), yaw.sin());
    let (cp, sp) = (pitch.cos(), pitch.sin());
    let q = [cp * p[0] - sp * p[2], p[1], sp * p[0] + cp * p[2]];
    [
        c * q[0] - s * q[1] + offset[0],
        s * q[0] + c * q[1] + offset[1],
        q[2] + offset[2],
    ]
}
fn source_point(p: [f64; 3]) -> V {
    [
        (p[0] * 0.01) as f32,
        (p[2] * 0.01) as f32,
        (-p[1] * 0.01) as f32,
        0.,
    ]
}
fn source_direction(p: [f64; 3]) -> V {
    [p[0] as f32, p[2] as f32, -p[1] as f32, 0.]
}
fn overlap(t: &Triangle, b: Bounds) -> bool {
    (0..3).all(|i| t.iter().any(|p| p[i] <= b.max[i]) && t.iter().any(|p| p[i] >= b.min[i]))
}
fn authored(tris: &[Triangle]) -> (StaticProvider, usize) {
    let owner = Bounds {
        min: std::array::from_fn(|i| {
            tris.iter()
                .flatten()
                .map(|p| p[i])
                .fold(f64::INFINITY, f64::min)
                - 100.
        }),
        max: std::array::from_fn(|i| {
            tris.iter()
                .flatten()
                .map(|p| p[i])
                .fold(f64::NEG_INFINITY, f64::max)
                + 100.
        }),
    };
    let patch = exposed_edges::derive(
        tris,
        owner,
        &mut |b| tris.iter().copied().filter(|t| overlap(t, b)).collect(),
        &Config {
            truck_distance_cm: f64::from(TRUCK) * 100.,
            tolerance_cm: 1e-5,
        },
    )
    .unwrap();
    let snapshot = Snapshot {
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
                truck_distance_cm: f64::from(TRUCK) * 100.,
                tolerance_cm: 1e-5,
            },
            cells: vec![],
            segments: patch.segments.len(),
        },
        path: PathBuf::from("synthetic-unused.json"),
        manifest_sha256: "c".repeat(64),
        cells: vec![Arc::new(patch.segments)],
        stats: json!({}),
    };
    let (rails, _) = exposed_edge_cache::rails(&snapshot).unwrap();
    let rails: Vec<_> = rails
        .into_iter()
        .enumerate()
        .map(|(i, points)| skate_data::skate_map::Rail {
            name: format!("synthetic-seam-{i}"),
            points,
            closed: false,
            native: None,
        })
        .collect();
    let provider = StaticProvider::authored(&rails).unwrap();
    let primitives = provider.primitives().len();
    (provider, primitives)
}

fn contact(board: [V; 4], edges: &[grind_contact::Primitive]) -> bool {
    if !grind_contact::truck_contacts(board, 0, WHEEL, TRUCK, edges)
        .iter()
        .all(Option::is_some)
    {
        return false;
    }
    let q = investigator::Query {
        board,
        admission: Admission {
            category: 400,
            state: 401,
            speed: 3.,
            velocity: board[2].map(|x| x * 3.),
            threshold_vs_slope: &GRAPH,
        },
        flags_2468: 0,
        flags_2472: 0,
        flags_2476: 0,
        flags_2484: 0,
        tip_state: 0,
        truck_to_wheel: WHEEL,
        deck_to_truck: TRUCK,
        test_above: 0.04,
        test_below: 0.04,
        translation: 0.,
        stability_nudge: 0.,
        balance: 0.,
        reference_right: board[0],
        ground_frames: 0,
        low_wheel_frames: 0,
        forbidden: false,
    };
    investigator::investigate(&q, edges)
        .candidate
        .is_some_and(|c| c.kind == 0 && c.entry_kind == EntryKind::StayInGrind)
}
#[test]
#[ignore = "external caller-owned dense fixture, diagnostic only"]
fn dense_owned_fixture_native_cap_contacts() {
    let input = std::env::var("S3_EDGE_AUDIT_INPUT").unwrap();
    let output = std::env::var("S3_EDGE_DENSITY_OUTPUT").unwrap();
    let v: Value = serde_json::from_slice(&std::fs::read(input).unwrap()).unwrap();
    let fixture = v["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .find(|v| {
            v["mesh"]
                .as_str()
                .unwrap()
                .contains("SM_Dec_Workshop_Pot_Bench.")
        })
        .unwrap();
    let raw: Vec<Triangle> = serde_json::from_value(fixture["triangles_cm"].clone()).unwrap();
    let mut rows = Vec::new();
    let lo: [f64; 3] = std::array::from_fn(|i| {
        raw.iter()
            .flatten()
            .map(|p| p[i])
            .fold(f64::INFINITY, f64::min)
    });
    let hi: [f64; 3] = std::array::from_fn(|i| {
        raw.iter()
            .flatten()
            .map(|p| p[i])
            .fold(f64::NEG_INFINITY, f64::max)
    });
    for with_neighbor in [false, true] {
        for yaw in [0., 37f64.to_radians()] {
            let mut tris = raw.clone();
            if with_neighbor {
                tris.extend(cube(
                    [lo[0] - 100., lo[1] - 50., lo[2]],
                    [hi[0] + 100., lo[1] - 20., hi[2]],
                ));
            }
            let tris: Vec<_> = tris
                .into_iter()
                .map(|t| t.map(|p| transform(p, yaw, 0., [0.; 3])))
                .collect();
            let (provider, _) = authored(&tris);
            let mut stations = 0usize;
            let mut full_contacts = 0usize;
            let mut max_query = 0usize;
            let mut capped_failures = Vec::new();
            for (index, p) in provider.primitives().iter().enumerate() {
                let d: [f32; 3] = std::array::from_fn(|i| p.end[i] - p.start[i]);
                let len = d.iter().map(|v| v * v).sum::<f32>().sqrt();
                if len < 0.001 || d[1].abs() / len > 0.8 {
                    continue;
                }
                let f: V = [d[0] / len, d[1] / len, d[2] / len, 0.];
                let side_len = (f[0] * f[0] + f[2] * f[2]).sqrt();
                let right: V = [f[2] / side_len, 0., -f[0] / side_len, 0.];
                let up: V = [
                    f[1] * right[2],
                    f[2] * right[0] - f[0] * right[2],
                    -f[1] * right[0],
                    0.,
                ];
                for fraction in [0.1f32, 0.3, 0.5, 0.7, 0.9] {
                    let c: V = std::array::from_fn(|i| {
                        if i == 3 {
                            0.
                        } else {
                            p.start[i] + fraction * d[i] + up[i] * 0.06
                        }
                    });
                    let board = [right, up, f, c];
                    stations += 1;
                    let ids = provider
                        .query(
                            std::array::from_fn(|i| c[i] - 1.2),
                            std::array::from_fn(|i| c[i] + 1.2),
                        )
                        .unwrap();
                    max_query = max_query.max(ids.len());
                    if contact(board, provider.primitives()) {
                        full_contacts += 1;
                        let local: Vec<_> = ids.iter().map(|&i| provider.primitives()[i]).collect();
                        if !contact(board, &local) {
                            capped_failures.push(json!({"primitive":index,"fraction":fraction,"point":c,"queried":ids,"start":p.start,"end":p.end}));
                        }
                    }
                }
            }
            let row = json!({"mesh":fixture["mesh"],"synthetic_neighbor":with_neighbor,"yaw":yaw,"triangles":tris.len(),"primitives":provider.primitives().len(),"stations":stations,"uncapped_valid_contacts":full_contacts,"max_native_query":max_query,"capped_contact_failures":capped_failures});
            eprintln!(
                "DENSE_CONTACT_SUMMARY neighbor={with_neighbor} yaw={yaw} primitives={} full_contacts={full_contacts} max_query={max_query} failures={}",
                provider.primitives().len(),
                capped_failures.len()
            );
            rows.push(row);
        }
    }
    std::fs::write(
        output,
        serde_json::to_vec_pretty(&json!({"schema":1,"rows":rows})).unwrap(),
    )
    .unwrap();
}

#[test]
#[ignore = "external derived dense cell, native capped contact census only"]
fn recorded_dense_cell_contacts() {
    let input = std::env::var("S3_EDGE_DENSITY_CELL").unwrap();
    let output = std::env::var("S3_EDGE_DENSITY_OUTPUT").unwrap();
    let value: Value = serde_json::from_slice(&std::fs::read(&input).unwrap()).unwrap();
    let segments: Vec<exposed_edges::Segment> =
        serde_json::from_value(value["segments"].clone()).unwrap();
    let count = segments.len();
    let snapshot = Snapshot {
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
                truck_distance_cm: f64::from(TRUCK) * 100.,
                tolerance_cm: 1e-5,
            },
            cells: vec![],
            segments: count,
        },
        path: PathBuf::from("private-unused.json"),
        manifest_sha256: "c".repeat(64),
        cells: vec![Arc::new(segments)],
        stats: json!({}),
    };
    let (rails, stitch) = exposed_edge_cache::rails(&snapshot).unwrap();
    let rails: Vec<_> = rails
        .into_iter()
        .enumerate()
        .map(|(i, points)| skate_data::skate_map::Rail {
            name: format!("private-dense-{i}"),
            points,
            closed: false,
            native: None,
        })
        .collect();
    let provider = StaticProvider::authored(&rails).unwrap();
    let mut dense_provider = StaticProvider::authored(&rails).unwrap();
    dense_provider.enable_dense_authored().unwrap();
    let mut eligible: Vec<_> = provider
        .primitives()
        .iter()
        .enumerate()
        .filter_map(|(index, p)| {
            let d: [f32; 3] = std::array::from_fn(|i| p.end[i] - p.start[i]);
            let len = d.iter().map(|v| v * v).sum::<f32>().sqrt();
            (len >= 0.6 && d[1].abs() / len <= 0.8).then_some(index)
        })
        .collect();
    let total_eligible = eligible.len();
    eligible.sort_by_key(|&i| (i as u64).wrapping_mul(0x9e3779b97f4a7c15));
    eligible.truncate(128);
    let mut valid = 0;
    let mut failed = Vec::new();
    let mut max_query = 0;
    let mut hit_cap = 0;
    let mut stations = 0;
    let bounds: Vec<_> = provider
        .primitives()
        .iter()
        .map(|p| octree::Bounds {
            min: std::array::from_fn(|i| p.start[i].min(p.end[i])),
            max: std::array::from_fn(|i| p.start[i].max(p.end[i])),
        })
        .collect();
    let tree = octree::Octree::new(
        bounds
            .iter()
            .copied()
            .reduce(octree::Bounds::union)
            .unwrap()
            .padded(),
        bounds,
    )
    .unwrap();
    let mut ranked_failures = Vec::new();
    let mut ranked_spurious = Vec::new();
    let mut ranked_query_ns = 0u128;
    let mut maximum_all_candidates = 0usize;
    for index in eligible {
        let p = &provider.primitives()[index];
        let d: [f32; 3] = std::array::from_fn(|i| p.end[i] - p.start[i]);
        let len = d.iter().map(|v| v * v).sum::<f32>().sqrt();
        let f: V = [d[0] / len, d[1] / len, d[2] / len, 0.];
        let side = (f[0] * f[0] + f[2] * f[2]).sqrt();
        let right: V = [f[2] / side, 0., -f[0] / side, 0.];
        let up: V = [
            f[1] * right[2],
            f[2] * right[0] - f[0] * right[2],
            -f[1] * right[0],
            0.,
        ];
        assert!(
            (0..3).map(|i| up[i] * f[i]).sum::<f32>().abs() < 1e-5,
            "synthetic board frame must be orthogonal"
        );
        for fraction in [0.25f32, 0.5, 0.75] {
            stations += 1;
            let c: V = std::array::from_fn(|i| {
                if i == 3 {
                    0.
                } else {
                    p.start[i] + fraction * d[i] + up[i] * 0.06
                }
            });
            let board = [right, up, f, c];
            let ids = provider
                .query(
                    std::array::from_fn(|i| c[i] - 1.2),
                    std::array::from_fn(|i| c[i] + 1.2),
                )
                .unwrap();
            max_query = max_query.max(ids.len());
            if ids.len() == 40 {
                hit_cap += 1;
            }
            // Private evaluation only: keep the native forty limit, select the
            // nearest segments when crowded, and restore their traversal order.
            // The production provider is deliberately unchanged by this test.
            let start = std::time::Instant::now();
            let all = tree.query(
                octree::Bounds {
                    min: std::array::from_fn(|i| c[i] - 1.2),
                    max: std::array::from_fn(|i| c[i] + 1.2),
                },
                usize::MAX,
            );
            assert_eq!(&all[..all.len().min(40)], ids.as_slice());
            maximum_all_candidates = maximum_all_candidates.max(all.len());
            let mut selected: Vec<_> = all
                .iter()
                .enumerate()
                .map(|(order, &id)| {
                    let p = &provider.primitives()[id];
                    let d: [f64; 3] =
                        std::array::from_fn(|i| f64::from(p.end[i]) - f64::from(p.start[i]));
                    let q: [f64; 3] = std::array::from_fn(|i| {
                        (f64::from(c[i] - 1.2) + f64::from(c[i] + 1.2)) * 0.5
                            - f64::from(p.start[i])
                    });
                    let t = ((0..3).map(|i| q[i] * d[i]).sum::<f64>()
                        / d.iter().map(|v| v * v).sum::<f64>())
                    .clamp(0., 1.);
                    let dist = (0..3)
                        .map(|i| {
                            let center = (f64::from(c[i] - 1.2) + f64::from(c[i] + 1.2)) * 0.5;
                            let residual = center - (f64::from(p.start[i]) + d[i] * t);
                            residual * residual
                        })
                        .sum::<f64>();
                    (dist, id, order)
                })
                .collect();
            if selected.len() > 40 {
                selected.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
                selected.truncate(40);
                selected.sort_by_key(|p| p.2);
            }
            let ranked: Vec<_> = selected
                .iter()
                .map(|p| provider.primitives()[p.1])
                .collect();
            let production_ids = dense_provider
                .query(
                    std::array::from_fn(|i| c[i] - 1.2),
                    std::array::from_fn(|i| c[i] + 1.2),
                )
                .unwrap();
            assert_eq!(
                production_ids,
                selected.iter().map(|p| p.1).collect::<Vec<_>>(),
                "production bounded selection differs from full-enumeration oracle"
            );
            ranked_query_ns += start.elapsed().as_nanos();
            let full_contact = contact(board, provider.primitives());
            let ranked_contact = contact(board, &ranked);
            if full_contact && !ranked_contact {
                ranked_failures.push(
                    json!({"primitive":index,"fraction":fraction,"point":c,"candidates":all.len()}),
                );
            }
            if ranked_contact && !full_contact {
                ranked_spurious.push(json!({"primitive":index,"fraction":fraction,"point":c}));
            }
            if full_contact {
                valid += 1;
                let local: Vec<_> = ids.iter().map(|&i| provider.primitives()[i]).collect();
                if !contact(board, &local) {
                    failed.push(json!({"primitive":index,"fraction":fraction,"point":c,"length_m":len,"target_in_query":ids.contains(&index),"query":ids}));
                }
            }
        }
    }
    let report = json!({"schema":1,"cell_key":value["key"],"derived_segments":count,"stitch":stitch,"primitives":provider.primitives().len(),"eligible_segments_60cm":total_eligible,"stations":stations,"uncapped_valid_contacts":valid,"maximum_native_query":max_query,"stations_hitting_cap":hit_cap,"capped_contact_failures":failed,
        "private_nearest_trial":{"candidate_production_verified":true,"maximum_all_candidates":maximum_all_candidates,"query_total_ms":ranked_query_ns as f64/1e6,"lost_uncapped_contacts":ranked_failures,"contacts_absent_from_uncapped":ranked_spurious}});
    eprintln!(
        "REAL_DENSE_CONTACT segments={count} eligible={total_eligible} stations={stations} valid={valid} capped={hit_cap} failures={}",
        failed.len()
    );
    std::fs::write(output, serde_json::to_vec_pretty(&report).unwrap()).unwrap();
}

#[cfg(windows)]
fn private_memory() -> Value {
    #[repr(C)]
    struct Counters {
        cb: u32,
        faults: u32,
        sizes: [usize; 9],
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetCurrentProcess() -> *mut std::ffi::c_void;
        fn K32GetProcessMemoryInfo(
            process: *mut std::ffi::c_void,
            counters: *mut Counters,
            size: u32,
        ) -> i32;
    }
    let mut c = Counters {
        cb: std::mem::size_of::<Counters>() as u32,
        faults: 0,
        sizes: [0; 9],
    };
    let ok = unsafe {
        K32GetProcessMemoryInfo(
            GetCurrentProcess(),
            &mut c,
            std::mem::size_of::<Counters>() as u32,
        )
    };
    assert_ne!(ok, 0);
    json!({"private_bytes":c.sizes[8],"working_set_bytes":c.sizes[1],"peak_working_set_bytes":c.sizes[0],"peak_commit_bytes":c.sizes[7]})
}
#[cfg(not(windows))]
fn private_memory() -> Value {
    Value::Null
}
#[test]
#[ignore = "private bounded provider scale benchmark; source queries unchanged"]
fn owned_provider_scale() {
    let input = std::env::var("S3_EDGE_DENSITY_CELL").unwrap();
    let output = std::env::var("S3_EDGE_DENSITY_OUTPUT").unwrap();
    let target: usize = std::env::var("S3_EDGE_PROVIDER_TARGET")
        .unwrap_or_else(|_| "1000000".into())
        .parse()
        .unwrap();
    assert!(target <= 1_000_000);
    let value: Value = serde_json::from_slice(&std::fs::read(input).unwrap()).unwrap();
    let segments: Vec<exposed_edges::Segment> =
        serde_json::from_value(value["segments"].clone()).unwrap();
    drop(value);
    let count = segments.len();
    let snapshot = Snapshot {
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
                truck_distance_cm: f64::from(TRUCK) * 100.,
                tolerance_cm: 1e-5,
            },
            cells: vec![],
            segments: count,
        },
        path: PathBuf::from("private-unused.json"),
        manifest_sha256: "c".repeat(64),
        cells: vec![Arc::new(segments)],
        stats: json!({}),
    };
    let begin = std::time::Instant::now();
    let (base, stitch) = exposed_edge_cache::rails(&snapshot).unwrap();
    let stitch_ms = begin.elapsed().as_secs_f64() * 1000.;
    let before = private_memory();
    let mut rails = Vec::new();
    let mut total = 0usize;
    let mut copy = 0;
    while total < target {
        for points in &base {
            let remaining = target - total;
            let keep = points.len().min(remaining + 1);
            if keep < 2 {
                continue;
            }
            let points: Vec<_> = points[..keep]
                .iter()
                .map(|p| {
                    [
                        p[0] + (copy % 32) as f32 * 100.,
                        p[1],
                        p[2] + (copy / 32) as f32 * 100.,
                    ]
                })
                .collect();
            total += points.len() - 1;
            rails.push(skate_data::skate_map::Rail {
                name: format!("private-benchmark-{}", rails.len()),
                points,
                closed: false,
                native: None,
            });
            if total == target {
                break;
            }
        }
        copy += 1;
    }
    let inputs = private_memory();
    let begin = std::time::Instant::now();
    let mut provider = StaticProvider::authored(&rails).unwrap();
    provider.enable_dense_authored().unwrap();
    let build_ms = begin.elapsed().as_secs_f64() * 1000.;
    let after = private_memory();
    let count = provider.primitives().len();
    assert_eq!(count, target);
    let mut samples = Vec::new();
    let mut cap = 0;
    let begin = std::time::Instant::now();
    for i in 0..1000 {
        let p = &provider.primitives()[i * 7919 % count];
        let c: [f32; 3] = std::array::from_fn(|j| (p.start[j] + p.end[j]) * 0.5);
        let at = std::time::Instant::now();
        let found = provider
            .query(c.map(|v| v - 1.2), c.map(|v| v + 1.2))
            .unwrap();
        assert!(found.len() <= 40);
        if found.len() == 40 {
            cap += 1;
        }
        samples.push(at.elapsed().as_nanos() as u64);
    }
    let query_ms = begin.elapsed().as_secs_f64() * 1000.;
    samples.sort_unstable();
    let report = json!({"schema":1,"segments":count,"rails":rails.len(),"input_cell_segments":snapshot.manifest.segments,"cell_stitch_ms":stitch_ms,"stitch":stitch,"build_ms":build_ms,"memory_before_expansion":before,"memory_expanded_rails":inputs,"memory_provider_and_rails":after,"primitive_bytes":std::mem::size_of::<grind_contact::Primitive>(),"metadata_bytes":std::mem::size_of::<spline::PrimitiveMetadata>(),"queries":1000,"queries_at_cap":cap,"query_total_ms":query_ms,"query_p95_us":samples[949]as f64/1000.,"query_max_us":samples[999]as f64/1000.});
    eprintln!("PROVIDER_SCALE {report}");
    std::fs::write(output, serde_json::to_vec_pretty(&report).unwrap()).unwrap();
}
