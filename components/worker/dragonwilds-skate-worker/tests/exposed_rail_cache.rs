//! Synthetic fixtures only. This tests exact derived-cache persistence without
//! loading game assets, extracting geometry or creating a physics session.
mod exposed_edge_cache {
    use std::path::PathBuf;
    pub struct Manifest {
        pub algorithm_sha256: String,
        pub segments: usize,
    }
    pub struct Snapshot {
        pub path: PathBuf,
        pub manifest_sha256: String,
        pub manifest: Manifest,
    }
}
#[path = "../src/exposed_rail_cache.rs"]
mod exposed_rail_cache;
use exposed_edge_cache::{Manifest, Snapshot};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};

struct Fixture {
    root: PathBuf,
    snapshot: Snapshot,
}
impl Fixture {
    fn new(segments: usize) -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let root = std::env::temp_dir().join(format!(
            "dws-native-rail-cache-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        let path = root.join("base.json");
        fs::write(&path, b"synthetic verified DWE identity").unwrap();
        Self {
            root,
            snapshot: Snapshot {
                path,
                manifest_sha256: "a".repeat(64),
                manifest: Manifest {
                    algorithm_sha256: "b".repeat(64),
                    segments,
                },
            },
        }
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let path = self.root.canonicalize().unwrap();
        assert!(path.starts_with(std::env::temp_dir().canonicalize().unwrap()));
        assert!(
            path.file_name()
                .unwrap()
                .to_str()
                .unwrap()
                .starts_with("dws-native-rail-cache-")
        );
        fs::remove_dir_all(path).unwrap();
    }
}
fn rails() -> Vec<Vec<[f32; 3]>> {
    vec![
        vec![[7., -0., 5.], [7.25, 0., 5.], [7.5, 0.0001, 5.]],
        vec![[1., 2., 3.], [f32::from_bits(1f32.to_bits() + 1), 2., 3.]],
        vec![[9., 1., 2.], [3., 2., 1.]],
    ]
}
fn stats(segments: usize) -> Value {
    json!({"provider_segments": segments, "stitched_joints": 1, "retained_components": 2, "excluded_length_m": 0.125})
}
fn bits(rails: &[Vec<[f32; 3]>]) -> Vec<Vec<[u32; 3]>> {
    rails
        .iter()
        .map(|r| r.iter().map(|p| p.map(f32::to_bits)).collect())
        .collect()
}
fn hash(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|v| format!("{v:02x}"))
        .collect()
}
fn rehash(bytes: &mut [u8]) {
    let digest = Sha256::digest(&bytes[128..]);
    bytes[96..128].copy_from_slice(&digest);
}

#[test]
fn exact_roundtrip_preserves_order_float_bits_stats_and_independent_record() {
    let f = Fixture::new(12);
    assert!(exposed_rail_cache::load(&f.snapshot).unwrap().is_none());
    assert!(!f.root.join("native-rails").exists());
    let original = rails();
    let metadata = stats(4);
    let record = exposed_rail_cache::store(&f.snapshot, &original, &metadata).unwrap();
    let (loaded, loaded_stats) = exposed_rail_cache::load(&f.snapshot).unwrap().unwrap();
    assert_eq!(bits(&loaded), bits(&original));
    assert_eq!(loaded_stats, metadata);
    assert_eq!(record.rails, 3);
    assert_eq!(record.points, 7);
    assert_eq!(record.segments, 4);
    let bytes = fs::read(&record.path).unwrap();
    assert_eq!(record.bytes, bytes.len() as u64);
    assert_eq!(record.sha256, hash(&bytes));
    assert_eq!(record.manifest_sha256, f.snapshot.manifest_sha256);
    assert_eq!(
        record.algorithm_sha256,
        f.snapshot.manifest.algorithm_sha256
    );
    assert!(record.ready);
    assert!(!record.cache_hit);
    let (_, _, warm) = exposed_rail_cache::load_with_record(&f.snapshot)
        .unwrap()
        .unwrap();
    assert!(warm.ready);
    assert!(warm.cache_hit);
    assert_eq!(warm.sha256, record.sha256);
    assert_eq!(warm.bytes, record.bytes);
    assert_eq!(warm.path, record.path);
    assert_eq!(
        record.path,
        f.root
            .join("native-rails")
            .join(format!("{}.dwr1", f.snapshot.manifest_sha256))
    );
    assert_eq!(
        fs::read(&f.snapshot.path).unwrap(),
        b"synthetic verified DWE identity"
    );
}

#[cfg(windows)]
#[test]
fn windows_public_record_preserves_ordinary_dwe_path_for_python() {
    let f = Fixture::new(12);
    assert!(!f.snapshot.path.to_string_lossy().starts_with(r"\\?\"));
    assert!(
        f.snapshot
            .path
            .canonicalize()
            .unwrap()
            .to_string_lossy()
            .starts_with(r"\\?\")
    );
    let cold = exposed_rail_cache::store(&f.snapshot, &rails(), &stats(4)).unwrap();
    let (_, _, warm) = exposed_rail_cache::load_with_record(&f.snapshot)
        .unwrap()
        .unwrap();
    let expected = f
        .snapshot
        .path
        .parent()
        .unwrap()
        .join("native-rails")
        .join(format!("{}.dwr1", f.snapshot.manifest_sha256));
    for record in [cold, warm] {
        assert_eq!(record.path, expected);
        let serialized = serde_json::to_value(record).unwrap();
        assert_eq!(
            serialized["path"].as_str().unwrap(),
            expected.to_str().unwrap()
        );
        assert!(!serialized["path"].as_str().unwrap().starts_with(r"\\?\"));
    }
}

#[test]
fn identities_version_size_and_checksum_are_required() {
    let mut f = Fixture::new(12);
    let record = exposed_rail_cache::store(&f.snapshot, &rails(), &stats(4)).unwrap();
    let original = fs::read(&record.path).unwrap();
    for (label, offset) in [
        ("version", 4),
        ("algorithm", 8),
        ("DWE", 40),
        ("payload", original.len() - 1),
    ] {
        let mut changed = original.clone();
        changed[offset] ^= 1;
        fs::write(&record.path, &changed).unwrap();
        assert!(exposed_rail_cache::load(&f.snapshot).is_err(), "{label}");
    }
    fs::write(&record.path, &original[..original.len() - 1]).unwrap();
    assert!(exposed_rail_cache::load(&f.snapshot).is_err());
    let mut changed = original.clone();
    changed.push(0);
    fs::write(&record.path, &changed).unwrap();
    assert!(exposed_rail_cache::load(&f.snapshot).is_err());
    fs::write(&record.path, &original).unwrap();
    f.snapshot.manifest.algorithm_sha256 = "c".repeat(64);
    assert!(exposed_rail_cache::load(&f.snapshot).is_err());
    f.snapshot.manifest.algorithm_sha256 = "b".repeat(64);
    f.snapshot.manifest_sha256 = "d".repeat(64);
    assert!(exposed_rail_cache::load(&f.snapshot).unwrap().is_none());
    let other = record
        .path
        .with_file_name(format!("{}.dwr1", f.snapshot.manifest_sha256));
    fs::write(other, &original).unwrap();
    assert!(exposed_rail_cache::load(&f.snapshot).is_err());
}

#[test]
fn rehashed_invalid_counts_coordinates_and_zero_primitives_still_rejected() {
    let f = Fixture::new(12);
    let record = exposed_rail_cache::store(&f.snapshot, &rails(), &stats(4)).unwrap();
    let original = fs::read(&record.path).unwrap();
    let stats_len = u64::from_le_bytes(original[88..96].try_into().unwrap()) as usize;
    let first_count = 128 + stats_len;
    let first_point = first_count + 8;
    let mut bad_length = original.clone();
    bad_length[first_count..first_count + 8].copy_from_slice(&u64::MAX.to_le_bytes());
    let mut bad_point = original.clone();
    bad_point[first_point..first_point + 4].copy_from_slice(&f32::NAN.to_le_bytes());
    let mut zero = original.clone();
    zero[first_point + 12..first_point + 24]
        .copy_from_slice(&original[first_point..first_point + 12]);
    for mut changed in [bad_length, bad_point, zero] {
        rehash(&mut changed);
        fs::write(&record.path, &changed).unwrap();
        assert!(exposed_rail_cache::load(&f.snapshot).is_err());
    }
    let mut huge = original.clone();
    huge[80..88].copy_from_slice(&u64::MAX.to_le_bytes());
    fs::write(&record.path, huge).unwrap();
    assert!(exposed_rail_cache::load(&f.snapshot).is_err());
    let mut false_stats = original.clone();
    let text = std::str::from_utf8(&original[128..128 + stats_len]).unwrap();
    let offset = text.find("\"provider_segments\":4").unwrap() + "\"provider_segments\":".len();
    false_stats[128 + offset] = b'9';
    rehash(&mut false_stats);
    fs::write(&record.path, false_stats).unwrap();
    assert!(exposed_rail_cache::load(&f.snapshot).is_err());
}

#[test]
fn corrupt_cache_can_be_replaced_but_invalid_publication_preserves_current_cache() {
    let f = Fixture::new(12);
    let record = exposed_rail_cache::store(&f.snapshot, &rails(), &stats(4)).unwrap();
    fs::write(&record.path, b"interrupted or corrupt").unwrap();
    assert!(exposed_rail_cache::load(&f.snapshot).is_err());
    exposed_rail_cache::store(&f.snapshot, &rails(), &stats(4)).unwrap();
    let before = fs::read(&record.path).unwrap();
    assert!(exposed_rail_cache::store(&f.snapshot, &rails(), &stats(99)).is_err());
    let invalid = vec![vec![[0., 0., 0.], [-0., 0., 0.]]];
    assert!(exposed_rail_cache::store(&f.snapshot, &invalid, &stats(1)).is_err());
    assert_eq!(fs::read(&record.path).unwrap(), before);
    assert_eq!(
        fs::read_dir(record.path.parent().unwrap()).unwrap().count(),
        1
    );
}

#[test]
fn chunk_boundaries_empty_world_and_source_limits() {
    let f = Fixture::new(10_000);
    let original = vec![
        (0..9001)
            .map(|i| [i as f32 * 0.125, -0., 1.])
            .collect::<Vec<_>>(),
    ];
    exposed_rail_cache::store(&f.snapshot, &original, &stats(9000)).unwrap();
    assert_eq!(
        bits(&exposed_rail_cache::load(&f.snapshot).unwrap().unwrap().0),
        bits(&original)
    );
    let empty = Fixture::new(0);
    exposed_rail_cache::store(&empty.snapshot, &[], &stats(0)).unwrap();
    assert!(
        exposed_rail_cache::load(&empty.snapshot)
            .unwrap()
            .unwrap()
            .0
            .is_empty()
    );
    assert!(exposed_rail_cache::store(&empty.snapshot, &rails(), &stats(4)).is_err());
}
