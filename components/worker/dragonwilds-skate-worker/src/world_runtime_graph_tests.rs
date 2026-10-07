use super::*;
use crate::{
    exposed_edge_cache::{Cell, Manifest, Parameters},
    exposed_edges::Segment,
};
use serde_json::json;

struct Fixture {
    root: PathBuf,
    manifest: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let root = std::env::temp_dir().join(format!(
            "dws-runtime-graph-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        Self::at(root)
    }
    fn at(root: PathBuf) -> Self {
        let physical = root.join("physical");
        fs::create_dir_all(&physical).unwrap();
        let manifest = physical.join("world.json");
        fs::write(&manifest, b"fixture").unwrap();
        Self { root, manifest }
    }
    fn snapshot(&self, pin_byte: char, offset: f64) -> Snapshot {
        let segments = vec![Segment {
            a: [offset, 0., 0.],
            b: [offset + 100., 0., 0.],
        }];
        let mut cells = Vec::new();
        let mut records = Vec::new();
        for (key, identity, segments) in [
            ([0, 0, 0], pin_byte.to_string().repeat(64), segments),
            (
                [1, 0, 0],
                "e".repeat(64),
                vec![Segment {
                    a: [1000., 0., 0.],
                    b: [1100., 0., 0.],
                }],
            ),
        ] {
            records.push(Cell {
                key,
                min: [0.; 3],
                max: [1.; 3],
                dependencies: vec![],
                path: format!("payloads/{identity}.edges"),
                bytes: 64,
                sha256: identity,
                segments: segments.len(),
                census: json!({}),
            });
            cells.push(Arc::new(segments));
        }
        Snapshot {
            manifest: Manifest {
                magic: "DWE1".into(),
                schema: 1,
                complete: true,
                base_fingerprint: "a".repeat(64),
                scene_fingerprint: None,
                algorithm_sha256: "b".repeat(64),
                parameters: Parameters {
                    cell_size_cm: 3200.,
                    halo_cm: 100.,
                    truck_distance_cm: 24.3,
                    tolerance_cm: 1e-5,
                },
                cells: records,
                segments: 2,
            },
            path: self.root.join("unused-dwe.json"),
            manifest_sha256: pin_byte.to_string().repeat(64),
            cells,
            stats: json!({}),
        }
    }
    fn initial(&self, snapshot: &Snapshot) -> GraphState {
        let (mut index, cache) = build(&self.manifest, snapshot).unwrap();
        index.rebind_initial_owners().unwrap();
        GraphState::new(index, cache, true)
    }
    fn transition(&self, state: &GraphState, previous: &Snapshot, next: &Snapshot) -> GraphState {
        let mut lease = state.take(&previous.manifest_sha256).unwrap();
        let tx = lease
            .index
            .as_mut()
            .unwrap()
            .begin(
                &previous.manifest_sha256,
                next.manifest_sha256.clone(),
                &inputs(next).unwrap(),
            )
            .unwrap();
        lease.pending = Some(tx);
        let path = delta_location(&self.manifest, next, &state.cache).unwrap();
        lease
            .index
            .as_ref()
            .unwrap()
            .store_delta(
                &path,
                &state.cache.path,
                lease.pending.as_ref().unwrap(),
                &state.cache.algorithm_sha256,
            )
            .unwrap();
        let cache = record(
            &path,
            next,
            lease.index.as_ref().unwrap(),
            &state.cache.algorithm_sha256,
            false,
        )
        .unwrap();
        let tx = lease.pending.take().unwrap();
        lease.index.as_mut().unwrap().commit(tx).unwrap();
        GraphState::new(lease.detach().unwrap(), cache, false)
    }
}

#[test]
#[ignore = "Export a fresh synthetic Rust graph cache fixture for Python interoperability"]
fn export_native_graph_cache_fixture_for_python_validation() {
    let lab = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("physics24-lab");
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let fixture = Fixture::at(lab.join(format!(
        "graph-cross-language-{}-{nonce}",
        std::process::id()
    )));
    let initial = fixture.snapshot('c', 0.);
    let moved = fixture.snapshot('d', 200.);
    let state = fixture.initial(&initial);
    let accepted = fixture.transition(&state, &initial, &moved);
    let records = json!({"data_root":fixture.root,"initial":state.cache.value().unwrap(),"next":accepted.cache.value().unwrap(),
        "initial_exposed":{"sha256":initial.manifest_sha256,"algorithm_sha256":initial.manifest.algorithm_sha256,"segments":initial.manifest.segments},
        "next_exposed":{"sha256":moved.manifest_sha256,"algorithm_sha256":moved.manifest.algorithm_sha256,"segments":moved.manifest.segments}});
    let path = fixture.root.join("records.json");
    fs::write(&path, serde_json::to_vec_pretty(&records).unwrap()).unwrap();
    eprintln!("NATIVE_GRAPH_FIXTURE {}", path.display());
    // This explicit ignored exporter intentionally preserves just its fresh
    // synthetic folder; ordinary ownership tests still clean their fixtures.
    std::mem::forget(fixture);
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.root).unwrap();
    }
}

#[test]
fn initial_cache_and_lease_restore_without_cloning_the_accepted_graph() {
    let fixture = Fixture::new();
    let initial = fixture.snapshot('c', 0.);
    let state = fixture.initial(&initial);
    let (cache, record) = load(&fixture.manifest, &initial).unwrap();
    assert_eq!(cache.rail_count(), 2);
    assert_eq!(record.format, "incremental_grind_v1");
    assert!(record.cache_hit);
    let lease = state.take(&initial.manifest_sha256).unwrap();
    assert!(state.index.lock().unwrap().is_none());
    assert!(state.take(&initial.manifest_sha256).is_err());
    drop(lease);
    assert!(state.index.lock().unwrap().is_some());
}

#[test]
fn a_failed_provider_or_persistence_step_rolls_back_and_returns_exact_owners() {
    let fixture = Fixture::new();
    let initial = fixture.snapshot('c', 0.);
    let moved = fixture.snapshot('d', 200.);
    let state = fixture.initial(&initial);
    let rails = state.index.lock().unwrap().as_ref().unwrap().rails();
    {
        let mut lease = state.take(&initial.manifest_sha256).unwrap();
        let tx = lease
            .index
            .as_mut()
            .unwrap()
            .begin(
                &initial.manifest_sha256,
                moved.manifest_sha256.clone(),
                &inputs(&moved).unwrap(),
            )
            .unwrap();
        assert_eq!(tx.delta.retired_owners, vec![1]);
        lease.pending = Some(tx);
    }
    let mut lease = state.take(&initial.manifest_sha256).unwrap();
    assert_eq!(lease.index.as_ref().unwrap().rails(), rails);
    let tx = lease
        .index
        .as_mut()
        .unwrap()
        .begin(
            &initial.manifest_sha256,
            moved.manifest_sha256.clone(),
            &inputs(&moved).unwrap(),
        )
        .unwrap();
    assert_eq!(tx.delta.retired_owners, vec![1]);
    assert_eq!(tx.delta.rails[0].0, 3);
    lease.pending = Some(tx);
}

#[test]
fn abandoning_a_built_initial_candidate_recovers_the_original_session_binding() {
    let fixture = Fixture::new();
    let initial = fixture.snapshot('c', 0.);
    let moved = fixture.snapshot('d', 200.);
    let state = fixture.initial(&initial);
    let candidate = fixture.transition(&state, &initial, &moved);
    drop(candidate);
    assert!(state.index.lock().unwrap().is_none());
    let mut lease = state.take(&initial.manifest_sha256).unwrap();
    let tx = lease
        .index
        .as_mut()
        .unwrap()
        .begin(
            &initial.manifest_sha256,
            moved.manifest_sha256.clone(),
            &inputs(&moved).unwrap(),
        )
        .unwrap();
    assert_eq!(tx.delta.retired_owners, vec![1]);
    assert_eq!(tx.delta.rails[0].0, 3);
    lease.pending = Some(tx);
}

#[test]
fn abandoning_a_built_delta_candidate_recovers_sparse_live_session_owners() {
    let fixture = Fixture::new();
    let initial = fixture.snapshot('c', 0.);
    let moved = fixture.snapshot('d', 200.);
    let again = fixture.snapshot('f', 400.);
    let state = fixture.initial(&initial);
    let accepted = fixture.transition(&state, &initial, &moved);
    let candidate = fixture.transition(&accepted, &moved, &again);
    drop(candidate);
    let mut lease = accepted.take(&moved.manifest_sha256).unwrap();
    let tx = lease
        .index
        .as_mut()
        .unwrap()
        .begin(
            &moved.manifest_sha256,
            again.manifest_sha256.clone(),
            &inputs(&again).unwrap(),
        )
        .unwrap();
    assert_eq!(tx.delta.retired_owners, vec![3]);
    assert_eq!(tx.delta.rails[0].0, 4);
    lease.pending = Some(tx);
}

#[test]
fn returning_to_prior_geometry_never_replaces_an_ancestor_with_a_cycle() {
    let fixture = Fixture::new();
    let initial = fixture.snapshot('c', 0.);
    let moved = fixture.snapshot('d', 200.);
    let state = fixture.initial(&initial);
    let original = fs::read(&state.cache.path).unwrap();
    let accepted = fixture.transition(&state, &initial, &moved);
    let returned = fixture.transition(&accepted, &moved, &initial);
    assert_ne!(returned.cache.path, state.cache.path);
    assert_eq!(fs::read(&state.cache.path).unwrap(), original);
    let graph = GrindPathIndex::load(
        &returned.cache.path,
        &initial.manifest_sha256,
        &returned.cache.algorithm_sha256,
    )
    .unwrap();
    assert_eq!(
        graph.rails(),
        state
            .take(&initial.manifest_sha256)
            .unwrap()
            .index
            .as_ref()
            .unwrap()
            .rails()
    );
}

#[test]
fn changed_recovery_payload_fails_without_rebinding_unrelated_current_owners() {
    let fixture = Fixture::new();
    let initial = fixture.snapshot('c', 0.);
    let moved = fixture.snapshot('d', 200.);
    let state = fixture.initial(&initial);
    let accepted = fixture.transition(&state, &initial, &moved);
    fs::write(&state.cache.path, b"corrupt immutable graph fixture").unwrap();
    assert!(state.take(&initial.manifest_sha256).is_err());
    assert!(!state.leased.load(Ordering::Acquire));
    assert_eq!(
        accepted
            .index
            .lock()
            .unwrap()
            .as_ref()
            .unwrap()
            .snapshot_identity(),
        moved.manifest_sha256
    );
}
