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

#[path = "../src/exposed_resident.rs"]
mod exposed_resident;

#[test]
fn resident_add_move_delete_matches_verified_cold_reader() {
    let temp = Temp::new();
    let (path, base) = setup(&temp.0);
    let p = params();
    let mut old_world = base.clone();
    let mut old = exposed_edge_cache::prepare(&path, &base, &p, None, &[]).unwrap();
    let mut resident = exposed_resident::Resident::new(&old).unwrap();
    for (i, triangles) in [
        cube([100.,100.,100.],[180.,180.,400.]),
        cube([3290.,100.,100.],[3370.,180.,400.]),
        vec![],
    ].into_iter().enumerate() {
        let overlay = write_world(&temp.0.join(format!("scene{i}")), &[triangles], Some(&base));
        let world = Arc::new(CompactWorld::with_overlay(&base, &overlay).unwrap());
        let changes = world.difference_bounds(&old_world).unwrap();
        let (updated, next) = resident.prepare(&path, &world, &p, &old, &changes).unwrap();
        assert_eq!(updated.stats["resident_payload_reuse"], true);
        assert!(updated.stats["reused_cells"].as_u64().unwrap() >= 1);
        let remote = old.manifest.cells.iter().position(|c| c.key[0] == 3).unwrap();
        let new_remote = updated.manifest.cells.iter().position(|c| c.key[0] == 3).unwrap();
        assert!(Arc::ptr_eq(&old.cells[remote], &updated.cells[new_remote]));
        let cold_path = temp.0.join(format!("cold{i}/export/manifest.json"));
        let cold = exposed_edge_cache::prepare(&cold_path, &world, &p, None, &[]).unwrap();
        assert_eq!(bits(&updated), bits(&cold));
        assert_eq!(bits(&updated), bits(&exposed_edge_cache::load(&path, &world, &p).unwrap()));
        if i == 2 { assert_eq!(updated.manifest.segments, 4); }
        resident = next; old = updated; old_world = world;
    }
}

#[test]
fn payload_leases_deny_mutation_and_reject_prior_corruption() {
    let temp = Temp::new();
    let (path, base) = setup(&temp.0);
    let snapshot = exposed_edge_cache::prepare(&path, &base, &params(), None, &[]).unwrap();
    let payload = snapshot.path.parent().unwrap().join(&snapshot.manifest.cells[0].path);
    let original = fs::read(&payload).unwrap();
    let resident = exposed_resident::Resident::new(&snapshot).unwrap();
    assert!(fs::write(&payload, &original).is_err(), "leased payload must not allow a writer");
    assert!(fs::remove_file(&payload).is_err(), "leased payload must not allow deletion");
    drop(resident);
    let mut damaged = original.clone(); *damaged.last_mut().unwrap() ^= 1;
    fs::write(&payload, damaged).unwrap();
    assert!(exposed_resident::Resident::new(&snapshot).is_err());
    fs::write(&payload, original).unwrap();
    drop(exposed_resident::Resident::new(&snapshot).unwrap());
    fs::remove_file(&payload).unwrap();
    assert!(exposed_resident::Resident::new(&snapshot).is_err());
}

#[test]
fn resident_rejects_wrong_snapshot_without_publishing() {
    let temp = Temp::new();
    let (path, base) = setup(&temp.0);
    let p = params();
    let mut snapshot = exposed_edge_cache::prepare(&path, &base, &p, None, &[]).unwrap();
    let resident = exposed_resident::Resident::new(&snapshot).unwrap();
    snapshot.manifest_sha256 = "0".repeat(64);
    assert!(resident.prepare(&path, &base, &p, &snapshot, &[]).is_err());
}

#[test]
fn single_pass_load_matches_original_and_locks_verified_contents() {
    let temp = Temp::new();
    let (path, base) = setup(&temp.0);
    let p = params();
    let original = exposed_edge_cache::prepare(&path, &base, &p, None, &[]).unwrap();
    let (loaded, resident) = exposed_resident::Resident::load(&path, &base, &p).unwrap();
    assert_eq!(bits(&original), bits(&loaded));
    assert_eq!(original.manifest_sha256, loaded.manifest_sha256);
    assert_eq!(loaded.stats["rebuilt_cells"], 0);
    let payload = loaded.path.parent().unwrap().join(&loaded.manifest.cells[0].path);
    let mut bytes = fs::read(&payload).unwrap();
    assert!(fs::remove_file(&payload).is_err());
    drop(resident);
    *bytes.last_mut().unwrap() ^= 1;
    fs::write(&payload, bytes).unwrap();
    assert!(exposed_resident::Resident::load(&path, &base, &p).is_err());
}
