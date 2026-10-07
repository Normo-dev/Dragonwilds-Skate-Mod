//! Private, content-addressed cache of the example's derived grind rails.
//! The original finder still runs for new geometry. Damaged/old cache files
//! are discarded, and cache I/O failure never removes collision or grinding.
use std::{fs, io::{Read, Write}, path::{Path, PathBuf}};

type Rails = Vec<Vec<[f32; 3]>>;
const MAX_POINTS: usize = 2_000_000;
// Each cached rail needs at least two points. This limits cache memory only;
// larger valid derived worlds keep all rails and simply skip cache storage.
const MAX_RAILS: usize = MAX_POINTS / 2;
const HEADER: usize = 80;
// Increment when the source finder or coordinate conversion changes.
const VERSION: u32 = 3;

pub fn fingerprint(triangles: &[[[f32; 3]; 3]]) -> blake3::Hash {
    let mut hash = blake3::Hasher::new();
    hash.update(b"Dragonwilds Skate original rails v3\0");
    hash.update(&(triangles.len() as u32).to_le_bytes());
    // Hash exact float bits, independent of allocator layout or source path.
    for tri in triangles {
        let mut bytes = [0u8; 36];
        for (i, value) in tri.iter().flatten().enumerate() {
            bytes[i * 4..i * 4 + 4].copy_from_slice(&value.to_le_bytes());
        }
        hash.update(&bytes);
    }
    hash.finalize()
}

fn cache_path(source: &Path, key: blake3::Hash) -> PathBuf {
    source.parent().unwrap_or_else(|| Path::new("."))
        .join(".skate-rails-v3").join(format!("{}.s3r1", key.to_hex()))
}

fn read(path: &Path, key: blake3::Hash) -> Result<Rails, String> {
    let mut file = fs::File::open(path).map_err(|e| e.to_string())?;
    let length = file.metadata().map_err(|e| e.to_string())?.len() as usize;
    if !(HEADER..=HEADER + MAX_RAILS * 4 + MAX_POINTS * 12).contains(&length) {
        return Err("Rail cache size outside bounds".into());
    }
    let mut header = [0u8; HEADER];
    file.read_exact(&mut header).map_err(|e| e.to_string())?;
    let count = u32::from_le_bytes(header[40..44].try_into().unwrap()) as usize;
    let points = u32::from_le_bytes(header[44..48].try_into().unwrap()) as usize;
    if &header[..4] != b"S3R1"
        || u32::from_le_bytes(header[4..8].try_into().unwrap()) != VERSION
        || &header[8..40] != key.as_bytes()
        || count > MAX_RAILS || points > MAX_POINTS
        || length != HEADER + count * 4 + points * 12 {
        return Err("Rail cache header mismatch".into());
    }
    let mut payload = vec![0u8; length - HEADER];
    file.read_exact(&mut payload).map_err(|e| e.to_string())?;
    if blake3::hash(&payload).as_bytes() != &header[48..80] {
        return Err("Rail cache checksum mismatch".into());
    }
    let mut rails = Vec::with_capacity(count);
    let mut cursor = 0;
    let mut decoded_points = 0;
    for _ in 0..count {
        let bytes = payload.get(cursor..cursor + 4).ok_or("Truncated rail cache")?;
        let n = u32::from_le_bytes(bytes.try_into().unwrap()) as usize;
        cursor += 4;
        if n < 2 || n >= 100_000 || n > points.saturating_sub(decoded_points) {
            return Err("Invalid cached rail length".into());
        }
        let bytes = payload.get(cursor..cursor + n * 12).ok_or("Truncated rail points")?;
        let rail: Vec<[f32; 3]> = bytes.chunks_exact(12).map(|p|
            std::array::from_fn(|i| f32::from_le_bytes(p[i * 4..i * 4 + 4].try_into().unwrap()))).collect();
        if !rail.iter().all(crate::valid_point) { return Err("Invalid cached coordinates".into()); }
        rails.push(rail);
        cursor += n * 12;
        decoded_points += n;
    }
    if cursor != payload.len() || decoded_points != points { return Err("Rail cache counts mismatch".into()); }
    Ok(rails)
}

fn write(path: &Path, key: blake3::Hash, rails: &[Vec<[f32; 3]>]) -> Result<(), String> {
    let points = rails.iter().map(Vec::len).sum::<usize>();
    if rails.len() > MAX_RAILS || points > MAX_POINTS
        || rails.iter().any(|r| r.len() < 2 || r.len() >= 100_000 || !r.iter().all(crate::valid_point)) {
        return Err("Derived rails exceed cache bounds".into());
    }
    let mut payload = Vec::with_capacity(rails.len() * 4 + points * 12);
    for rail in rails {
        payload.extend_from_slice(&(rail.len() as u32).to_le_bytes());
        for value in rail.iter().flatten() { payload.extend_from_slice(&value.to_le_bytes()); }
    }
    let mut header = Vec::with_capacity(HEADER);
    header.extend_from_slice(b"S3R1");
    header.extend_from_slice(&VERSION.to_le_bytes());
    header.extend_from_slice(key.as_bytes());
    header.extend_from_slice(&(rails.len() as u32).to_le_bytes());
    header.extend_from_slice(&(points as u32).to_le_bytes());
    header.extend_from_slice(blake3::hash(&payload).as_bytes());
    fs::create_dir_all(path.parent().ok_or("Cache has no directory")?).map_err(|e| e.to_string())?;
    let temp = path.with_extension(format!("{}.tmp", std::process::id()));
    let result = (|| {
        let mut file = fs::File::create(&temp).map_err(|e| e.to_string())?;
        file.write_all(&header).and_then(|_| file.write_all(&payload))
            .and_then(|_| file.flush()).map_err(|e| e.to_string())?;
        drop(file);
        fs::rename(&temp, path).map_err(|e| e.to_string())
    })();
    if result.is_err() { let _ = fs::remove_file(&temp); }
    result
}

pub fn derive(source: Option<&Path>, triangles: &[[[f32; 3]; 3]]) -> Rails {
    let start = std::time::Instant::now();
    let cache = source.map(|p| { let key = fingerprint(triangles); (cache_path(p, key), key) });
    if let Some((path, key)) = &cache {
        match read(path, *key) {
            Ok(rails) => {
                eprintln!("SOURCE_RAILS_CACHE hit rails={} elapsed_ms={:.3}", rails.len(), start.elapsed().as_secs_f64() * 1000.);
                return rails;
            }
            Err(error) if path.exists() => eprintln!("SOURCE_RAILS_CACHE rejected: {error}"),
            Err(_) => (),
        }
    }
    let rails = crate::find_rails(triangles);
    if let Some((path, key)) = cache {
        if let Err(error) = write(&path, key, &rails) { eprintln!("SOURCE_RAILS_CACHE not saved: {error}"); }
    }
    eprintln!("SOURCE_RAILS_CACHE miss rails={} elapsed_ms={:.3}", rails.len(), start.elapsed().as_secs_f64() * 1000.);
    rails
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wide_rail_cache_roundtrip_and_previous_version_rejection() {
        let triangles = [[[0.,0.,0.], [1.,0.,0.], [0.,0.,1.]]];
        let rails: Rails = (0..66_000).map(|i| vec![[i as f32,0.,0.], [i as f32,0.,1.]]).collect();
        assert!(crate::validate_world(&triangles, &rails).is_ok());
        let key = fingerprint(&triangles);
        let folder = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join("persistent5-rail-cache-checks");
        let path = cache_path(&folder.join("fixture.triangles"), key);
        write(&path, key, &rails).unwrap();
        assert_eq!(read(&path, key).unwrap(), rails);
        let mut bytes = fs::read(&path).unwrap();
        bytes[4..8].copy_from_slice(&2u32.to_le_bytes());
        fs::write(&path, bytes).unwrap();
        assert!(read(&path, key).unwrap_err().contains("header mismatch"));
    }
}
