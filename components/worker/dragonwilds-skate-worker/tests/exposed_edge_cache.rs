#[path = "../src/compact_world.rs"]
mod compact_world;
#[path = "../src/exposed_edge_cache.rs"]
mod exposed_edge_cache;
#[path = "../src/exposed_edges.rs"]
mod exposed_edges;
#[path = "../src/exposed_query.rs"]
mod exposed_query;
use compact_world::CompactWorld;
use exposed_edge_cache::{Parameters, Snapshot};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
};
type Tri = [[f64; 3]; 3];
fn hash(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        static N: AtomicU64 = AtomicU64::new(0);
        let p = std::env::temp_dir().join(format!(
            "dws-exposed-cache-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        Self(p)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let p = self.0.canonicalize().unwrap();
        assert!(p.starts_with(std::env::temp_dir().canonicalize().unwrap()));
        assert!(
            p.file_name()
                .unwrap()
                .to_str()
                .unwrap()
                .starts_with("dws-exposed-cache-")
        );
        fs::remove_dir_all(p).unwrap();
    }
}
fn params() -> Parameters {
    Parameters {
        cell_size_cm: 3200.,
        halo_cm: 100.,
        truck_distance_cm: 24.3,
        tolerance_cm: 1e-5,
    }
}
fn cube(lo: [f64; 3], hi: [f64; 3]) -> Vec<Tri> {
    let p: Vec<_> = (0..8)
        .map(|n| std::array::from_fn(|i| if n & (1 << i) == 0 { lo[i] } else { hi[i] }))
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
    .map(|t| t.map(|i| p[i]))
    .collect()
}
fn file(root: &Path, name: &str, bytes: &[u8]) -> Value {
    fs::write(root.join(name), bytes).unwrap();
    json!({"path":name,"bytes":bytes.len(),"sha256":hash(bytes)})
}
fn write_world(root: &Path, meshes: &[Vec<Tri>], base: Option<&CompactWorld>) -> PathBuf {
    fs::create_dir_all(root).unwrap();
    let mut geometry = Vec::new();
    let mut records = Vec::new();
    let mut instances = Vec::new();
    let mut offset = 0;
    for (index, triangles) in meshes.iter().enumerate() {
        if triangles.is_empty() {
            continue;
        }
        records.push(
            json!({"id":index.to_string(),"offset":offset,"count":triangles.len(),"unsupported":0}),
        );
        offset += triangles.len();
        for v in triangles.iter().flatten().flatten() {
            geometry.extend(v.to_le_bytes());
        }
        instances.extend(
            ((index + base.map_or(0, |b| b.counts.geometry_triangles / 12)) as u32).to_le_bytes(),
        );
        instances.extend((index as u32).to_le_bytes());
        for row in [[1f64, 0., 0.], [0., 1., 0.], [0., 0., 1.]] {
            for v in row {
                instances.extend(v.to_le_bytes());
            }
        }
        for v in [0f64; 3] {
            instances.extend(v.to_le_bytes());
        }
        for lower in [true, false] {
            for axis in 0..3 {
                let v = triangles
                    .iter()
                    .flatten()
                    .map(|p| p[axis])
                    .reduce(|a, b| if lower { a.min(b) } else { a.max(b) })
                    .unwrap();
                instances.extend(v.to_le_bytes());
            }
        }
    }
    let gf = file(root, "geometry.f64", &geometry);
    let ins = file(root, "instances.bin", &instances);
    let fingerprint = hash(&[geometry.clone(), instances.clone()].concat());
    let completeness = json!({"complete":true,"missing_geometry_count":0,"unsupported_meshes":0,"unresolved_empty_meshes":0});
    let m = if let Some(base) = base {
        json!({"magic":"S3O1","schema":1,"base_fingerprint":base.source_fingerprint,"source_fingerprint":fingerprint,
        "packages":["/A"],"instance_packages":["/A","/B"],"geometry_base_count":2,"geometry":records,"geometry_file":gf,
        "instance_file":ins,"instance_count":instances.len()/152,"instance_stride":152,"triangle_count":offset,"default_shape_complexity":1,"completeness":completeness,"binary_bytes":geometry.len()+instances.len()})
    } else {
        json!({"magic":"S3W1","schema":1,"coordinate_system":"Unreal centimetres XYZ","byte_order":"little","source_fingerprint":fingerprint,"default_shape_complexity":1,
        "completeness":completeness,"geometry":records,"geometry_file":gf,"geometry_triangle_count":offset,"packages":["/A","/B"],"instance_file":ins,
        "instance_count":instances.len()/152,"instance_stride":152,"terrain":[],"terrain_triangle_count":0,"static_triangle_count":offset,"visibility_hole_threshold":170,"binary_bytes":geometry.len()+instances.len()})
    };
    let path = root.join("manifest.json");
    fs::write(&path, serde_json::to_vec(&m).unwrap()).unwrap();
    path
}
fn setup(root: &Path) -> (PathBuf, Arc<CompactWorld>) {
    let path = write_world(
        &root.join("export"),
        &[
            cube([100., 100., 100.], [180., 180., 300.]),
            cube([10100., 100., 100.], [10200., 200., 300.]),
        ],
        None,
    );
    let world = Arc::new(CompactWorld::load(&path).unwrap());
    (path, world)
}
fn bits(snapshot: &Snapshot) -> Vec<Vec<[u32; 3]>> {
    exposed_edge_cache::rails(snapshot)
        .unwrap()
        .0
        .into_iter()
        .map(|r| r.into_iter().map(|p| p.map(f32::to_bits)).collect())
        .collect()
}
#[test]
fn cold_warm_exact_order_and_corruption_repairs_only_one_cell() {
    let temp = Temp::new();
    let (path, world) = setup(&temp.0);
    let parameters = params();
    let a = exposed_edge_cache::prepare(&path, &world, &parameters, None, &[]).unwrap();
    assert_eq!(a.stats["extraction_calls"], 2);
    assert_eq!(a.manifest.segments, 8);
    let b = exposed_edge_cache::load(&path, &world, &parameters).unwrap();
    assert_eq!(b.stats["extraction_calls"], 0);
    assert_eq!(bits(&a), bits(&b));
    let payload = a.path.parent().unwrap().join(&a.manifest.cells[0].path);
    let mut bytes = fs::read(&payload).unwrap();
    *bytes.last_mut().unwrap() ^= 1;
    fs::write(&payload, bytes).unwrap();
    assert!(exposed_edge_cache::load(&path, &world, &parameters).is_err());
    let c = exposed_edge_cache::prepare(&path, &world, &parameters, None, &[]).unwrap();
    assert_eq!(c.stats["rebuilt_cells"], 1);
    assert_eq!(c.stats["reused_cells"], 1);
    assert_eq!(bits(&a), bits(&c));
}
#[test]
fn add_move_and_delete_overlay_match_cold_rebuild_without_touching_remote_cells() {
    let temp = Temp::new();
    let (path, base) = setup(&temp.0);
    let parameters = params();
    let mut old_world = base.clone();
    let mut old = exposed_edge_cache::prepare(&path, &base, &parameters, None, &[]).unwrap();
    let scenes = vec![
        cube([100., 100., 100.], [180., 180., 400.]),
        cube([3290., 100., 100.], [3370., 180., 400.]),
        vec![],
    ];
    for (i, triangles) in scenes.into_iter().enumerate() {
        let overlay = write_world(&temp.0.join(format!("scene{i}")), &[triangles], Some(&base));
        let world = Arc::new(CompactWorld::with_overlay(&base, &overlay).unwrap());
        let changes = world.difference_bounds(&old_world).unwrap();
        let updated =
            exposed_edge_cache::prepare(&path, &world, &parameters, Some(&old), &changes).unwrap();
        assert!(
            updated.stats["reused_cells"].as_u64().unwrap() >= 1,
            "remote fixed platform must be reused"
        );
        let coldpath = temp.0.join(format!("cold{i}/export/manifest.json"));
        let cold = exposed_edge_cache::prepare(&coldpath, &world, &parameters, None, &[]).unwrap();
        assert_eq!(bits(&updated), bits(&cold), "scene{i}");
        assert_eq!(
            bits(&updated),
            bits(&exposed_edge_cache::load(&path, &world, &parameters).unwrap())
        );
        if i == 2 {
            assert_eq!(
                updated.manifest.segments, 4,
                "deleted platform rails must vanish"
            );
        }
        old = updated;
        old_world = world;
    }
}
#[test]
fn seam_crossing_is_single_stitched_run_and_negative_cells_are_owned() {
    let temp = Temp::new();
    let triangles = cube([-6400., 100., 100.], [6400., 200., 300.]);
    let path = write_world(&temp.0.join("export"), &[triangles], None);
    let world = CompactWorld::load(&path).unwrap();
    let a = exposed_edge_cache::prepare(&path, &world, &params(), None, &[]).unwrap();
    assert!(a.manifest.cells.iter().any(|c| c.key[0] < 0));
    let (rails, stats) = exposed_edge_cache::rails(&a).unwrap();
    assert_eq!(rails.len(), 4, "{:?}", stats);
    assert_eq!(stats["duplicate_segments"], 0);
    assert!(stats["stitched_joints"].as_u64().unwrap() >= 6);
    assert_eq!(
        bits(&a),
        bits(&exposed_edge_cache::load(&path, &world, &params()).unwrap())
    );
}
#[test]
fn missing_manifest_cell_and_wrong_parameters_cannot_claim_complete() {
    let temp = Temp::new();
    let (path, world) = setup(&temp.0);
    let a = exposed_edge_cache::prepare(&path, &world, &params(), None, &[]).unwrap();
    let mut value: Value = serde_json::from_slice(&fs::read(&a.path).unwrap()).unwrap();
    let removed = value["cells"].as_array_mut().unwrap().pop().unwrap();
    value["segments"] =
        json!(value["segments"].as_u64().unwrap() - removed["segments"].as_u64().unwrap());
    fs::write(&a.path, serde_json::to_vec(&value).unwrap()).unwrap();
    assert!(exposed_edge_cache::load(&path, &world, &params()).is_err());
    let mut other = params();
    other.truck_distance_cm += 1.;
    assert!(exposed_edge_cache::load(&path, &world, &other).is_err());
}
#[test]
fn lost_retained_payload_is_restored_before_durable_scene_publication() {
    let temp = Temp::new();
    let (path, base) = setup(&temp.0);
    let parameters = params();
    let prior = exposed_edge_cache::prepare(&path, &base, &parameters, None, &[]).unwrap();
    let retained = &prior.manifest.cells[1];
    fs::remove_file(prior.path.parent().unwrap().join(&retained.path)).unwrap();
    let scene = write_world(
        &temp.0.join("scene"),
        &[cube([100., 100., 100.], [180., 180., 400.])],
        Some(&base),
    );
    let world = CompactWorld::with_overlay(&base, &scene).unwrap();
    let next = exposed_edge_cache::prepare(
        &path,
        &world,
        &parameters,
        Some(&prior),
        &world.changed_bounds(),
    )
    .unwrap();
    assert_eq!(next.stats["rebuilt_cells"], 1);
    assert_eq!(next.stats["reused_cells"], 1);
    assert_eq!(next.stats["repaired_payloads"], 1);
    assert_eq!(
        bits(&next),
        bits(&exposed_edge_cache::load(&path, &world, &parameters).unwrap())
    );
}
#[test]
fn submitted_point_identity_stitches_roundoff_without_bridging_real_gaps_and_order_is_stable() {
    use exposed_edges::Segment;
    let temp = Temp::new();
    let (path, world) = setup(&temp.0);
    let mut snapshot = exposed_edge_cache::prepare(&path, &world, &params(), None, &[]).unwrap();
    let a = Segment {
        a: [99_900., 180_000., 300.],
        b: [100_000., 180_000., 300.],
    };
    let b = Segment {
        a: [100_000.00000001, 180_000., 300.],
        b: [100_100., 180_000., 300.],
    };
    let close = |snap: &mut Snapshot, input: Vec<Segment>| {
        snap.manifest.segments = input.len();
        snap.cells = vec![Arc::new(input)];
        bits(snap)
    };
    let expected = close(&mut snapshot, vec![a, b]);
    assert_eq!(
        expected.len(),
        1,
        "same submitted source point must retain one path"
    );
    assert_eq!(
        expected,
        close(
            &mut snapshot,
            vec![Segment { a: b.b, b: b.a }, Segment { a: a.b, b: a.a }]
        )
    );
    let gap = Segment {
        a: [100_001., 180_000., 300.],
        ..b
    };
    assert_eq!(
        close(&mut snapshot, vec![a, gap]).len(),
        2,
        "one centimetre unsupported gap must remain a gap"
    );
    let collapsed = Segment {
        a: b.a,
        b: [100_000.00000002, 180_000., 300.],
    };
    assert_eq!(
        expected,
        close(&mut snapshot, vec![a, collapsed, b]),
        "sub-ULP edge may collapse without inserting a chord"
    );
}

#[test]
fn interrupted_offline_journal_resumes_verified_cells_and_never_claims_ready() {
    let temp = Temp::new();
    let (path, world) = setup(&temp.0);
    let p = params();
    let first = exposed_edge_cache::prepare(&path, &world, &p, None, &[]).unwrap();
    let journal = first.path.with_extension("incomplete.jsonl");
    let header = json!({"magic":"DWP1","schema":1,"complete":false,"base_fingerprint":world.source_fingerprint,"scene_fingerprint":world.scene_fingerprint,"algorithm_sha256":first.manifest.algorithm_sha256,"parameters":p});
    let mut data = serde_json::to_vec(&header).unwrap();
    data.push(b'\n');
    data.extend(serde_json::to_vec(&first.manifest.cells[0]).unwrap());
    data.extend(b"\n{\"unfinished\":");
    fs::write(&journal, data).unwrap();
    fs::remove_file(&first.path).unwrap();
    assert!(
        exposed_edge_cache::load(&path, &world, &p).is_err(),
        "partial work is never readiness"
    );
    let resumed = exposed_edge_cache::prepare(&path, &world, &p, None, &[]).unwrap();
    assert_eq!(resumed.stats["reused_cells"], 1);
    assert_eq!(resumed.stats["rebuilt_cells"], 1);
    assert_eq!(bits(&first), bits(&resumed));
    assert!(!journal.exists());
    // A different algorithm declaration is discarded even with plausible records.
    let mut wrong = header;
    wrong["algorithm_sha256"] = json!("f".repeat(64));
    let mut data = serde_json::to_vec(&wrong).unwrap();
    data.push(b'\n');
    data.extend(serde_json::to_vec(&first.manifest.cells[0]).unwrap());
    data.push(b'\n');
    fs::write(&journal, data).unwrap();
    fs::remove_file(&resumed.path).unwrap();
    let next = exposed_edge_cache::prepare(&path, &world, &p, None, &[]).unwrap();
    assert_eq!(next.stats["reused_cells"], 0);
    assert_eq!(next.stats["rebuilt_cells"], 2);
}

#[test]
fn minimum_applies_to_global_connected_components_before_branch_and_corner_splitting() {
    use exposed_edges::Segment;
    let temp = Temp::new();
    let (path, world) = setup(&temp.0);
    let mut snapshot = exposed_edge_cache::prepare(&path, &world, &params(), None, &[]).unwrap();
    let edge = |a, b| Segment { a, b };
    let input = vec![
        // A three-arm 60 cm network, each arm individually below the minimum.
        edge([0., 0., 0.], [20., 0., 0.]),
        edge([0., 0., 0.], [0., 20., 0.]),
        edge([0., 0., 0.], [-20., 0., 0.]),
        // A right-angle 60 cm route, stored in separate cells below.
        edge([100., 0., 0.], [130., 0., 0.]),
        edge([130., 0., 0.], [130., 30., 0.]),
        // A disconnected 40 cm network must disappear entirely.
        edge([200., 0., 0.], [220., 0., 0.]),
        edge([220., 0., 0.], [220., 20., 0.]),
        // An exact 50 cm standalone component is retained.
        edge([0., 100., 0.], [50., 100., 0.]),
        // A real gap cannot join two short pieces into an eligible component.
        edge([300., 0., 0.], [330., 0., 0.]),
        edge([330.01, 0., 0.], [360.01, 0., 0.]),
    ];
    snapshot.manifest.segments = input.len();
    snapshot.cells = vec![Arc::new(input[..4].to_vec()), Arc::new(input[4..].to_vec())];
    let (expected, stats) = exposed_edge_cache::rails(&snapshot).unwrap();
    assert_eq!(stats["retained_components"], 3);
    assert_eq!(stats["excluded_components"], 3);
    assert_eq!(stats["excluded_segments"], 4);
    assert_eq!(stats["provider_segments"], 6);
    assert_eq!(stats["minimum_connected_component_cm"], 50);
    let reversed: Vec<_> = input.into_iter().rev().map(|e| edge(e.b, e.a)).collect();
    snapshot.cells = vec![Arc::new(reversed)];
    assert_eq!(expected, exposed_edge_cache::rails(&snapshot).unwrap().0);
}

#[test]
fn short_curve_and_final_f32_cross_piece_continuation_survive_component_filter() {
    use exposed_edges::Segment;
    let temp = Temp::new();
    let (path, world) = setup(&temp.0);
    let mut snapshot = exposed_edge_cache::prepare(&path, &world, &params(), None, &[]).unwrap();
    let mut input = Vec::new();
    for i in 0..8 {
        let angle = i as f64 * std::f64::consts::FRAC_PI_4;
        let next = (i + 1) as f64 * std::f64::consts::FRAC_PI_4;
        let a = [10. * angle.cos(), 10. * angle.sin(), 0.];
        let b = if i == 7 {
            [10., 0., 0.]
        } else {
            [10. * next.cos(), 10. * next.sin(), 0.]
        };
        input.push(Segment { a, b });
    }
    // Independently transformed endpoints differ in f64 but are identical at
    // the exact f32 metre coordinates submitted to the source simulation.
    for i in 0..3 {
        input.push(Segment {
            a: [
                100_000. + i as f64 * 20. + if i > 0 { 1e-8 } else { 0. },
                180_000.,
                300.,
            ],
            b: [100_000. + (i + 1) as f64 * 20., 180_000., 300.],
        });
    }
    snapshot.manifest.segments = input.len();
    snapshot.cells = input.into_iter().map(|e| Arc::new(vec![e])).collect();
    let (_, stats) = exposed_edge_cache::rails(&snapshot).unwrap();
    assert_eq!(stats["retained_components"], 2);
    assert_eq!(stats["excluded_components"], 0);
    assert_eq!(stats["provider_segments"], 11);
}

#[test]
fn sparse_huge_group_occupancy_covers_lifted_edges_without_filling_empty_volume() {
    let temp = Temp::new();
    let triangles = cube(
        [-160_000., -160_000., -160_000.],
        [160_000., 160_000., 160_000.],
    );
    let path = write_world(&temp.0.join("large"), &[triangles.clone()], None);
    let world = CompactWorld::load(&path).unwrap();
    let keys = exposed_edge_cache::occupied(&world).unwrap();
    assert!(keys.len() < 20_000, "{}", keys.len());
    assert!(!keys.contains(&[0, 0, 0]));
    for t in &triangles {
        for e in 0..3 {
            for step in 0..=100 {
                let f = step as f64 / 100.;
                for shift in [[4., 0., 0.], [-4., 0., 0.], [0., 4., 0.], [0., 0., -4.]] {
                    let p: [f64; 3] = std::array::from_fn(|i| {
                        t[e][i] + (t[(e + 1) % 3][i] - t[e][i]) * f + shift[i]
                    });
                    let key = p.map(|v| (v / 3200.).floor() as i32);
                    assert!(keys.contains(&key), "missing {key:?}");
                }
            }
        }
    }
}

#[test]
fn small_group_occupancy_includes_supported_lift_across_cell_boundary() {
    let temp = Temp::new();
    let path = write_world(
        &temp.0.join("boundary"),
        &[cube([100., 100., 3150.], [300., 300., 3199.])],
        None,
    );
    let world = CompactWorld::load(&path).unwrap();
    let keys = exposed_edge_cache::occupied(&world).unwrap();
    assert!(keys.contains(&[0, 0, 0]));
    assert!(keys.contains(&[0, 0, 1]));
}

#[test]
fn bounded_parallel_cold_prepare_matches_serial_and_resumes_after_partial_failure() {
    let temp = Temp::new();
    let path = write_world(
        &temp.0.join("source"),
        &[
            cube([100., 100., 100.], [16000., 250., 300.]),
            cube([100., 4000., 100.], [16000., 4250., 350.]),
        ],
        None,
    );
    let world = CompactWorld::load(&path).unwrap();
    let mut p = params();
    // Exact owned DeckCenterToTruck f32 converted to centimetres. The default
    // JSON reader rounds its decimal one f64 bit; cache identity must still hit
    // without changing the actual source probe distance.
    p.truck_distance_cm = f64::from(0.243f32) * 100.;
    let serial_path = temp.0.join("serial/export/manifest.json");
    let parallel_path = temp.0.join("parallel/export/manifest.json");
    let failed_path = temp.0.join("interrupted/export/manifest.json");
    let serial =
        exposed_edge_cache::prepare_testing(&serial_path, &world, &p, None, &[], 1, None).unwrap();
    let parallel =
        exposed_edge_cache::prepare_testing(&parallel_path, &world, &p, None, &[], 4, None)
            .unwrap();
    assert_eq!(parallel.stats["derivation_workers"], 4);
    assert_eq!(
        fs::read(&serial.path).unwrap(),
        fs::read(&parallel.path).unwrap()
    );
    assert_eq!(bits(&serial), bits(&parallel));
    assert!(
        exposed_edge_cache::prepare_testing(&failed_path, &world, &p, None, &[], 4, Some(3))
            .is_err()
    );
    assert!(exposed_edge_cache::load(&failed_path, &world, &p).is_err());
    let resumed =
        exposed_edge_cache::prepare_testing(&failed_path, &world, &p, None, &[], 4, None).unwrap();
    assert_eq!(resumed.stats["reused_cells"], 3);
    assert_eq!(
        fs::read(&serial.path).unwrap(),
        fs::read(&resumed.path).unwrap()
    );
    assert_eq!(bits(&serial), bits(&resumed));
    let warm =
        exposed_edge_cache::prepare_testing(&failed_path, &world, &p, None, &[], 4, None).unwrap();
    assert_eq!(warm.stats["extraction_calls"], 0);
    assert_eq!(warm.manifest.parameters, p);
}
