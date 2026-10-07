#[path="../src/compact_world.rs"] mod compact_world;
#[path="../src/compact_geometry.rs"] mod compact_geometry;
use std::{path::PathBuf,sync::Arc,time::Instant};
use compact_world::CompactWorld;
use compact_geometry::CompactGeometry;
use skate_host::bridge::{Session,Controls};

/// Contact-stage verification only. Empty rails isolate world-query frame cost;
/// the shipping whole-world entry point requires the permanent grind bake.
#[test]
#[ignore="requires the locally extracted owned game assets and whole-world cache"]
fn full_world_neutral_session_and_original_camera_presets() {
    let work=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
    let start=Instant::now();
    let world=Arc::new(CompactWorld::load(&work.join("dragonwilds-map-data/compact-world-v1/manifest.json")).unwrap());
    let source=Arc::new(CompactGeometry::new(world,[0.;3]).unwrap());
    eprintln!("COMPACT_SESSION source_ms={}",start.elapsed().as_millis());
    // Known host-traced temple floor, from the previously validated dense area.
    let spawn=[53.601065934564,-36.323296520743+0.25,-1886.9363466957];
    let mut session=Session::new_with_source(&work.join("skate3-converted/assets"),source.clone(),vec![],spawn,0.).unwrap();
    eprintln!("COMPACT_SESSION init_ms={}",start.elapsed().as_millis());
    assert_eq!(session.camera_mode(),"high");
    let mut times=Vec::new();let mut low_camera=None;let mut high_camera=None;
    for tick in 0..360 {
        if tick==120 {session.set_low_camera(true);assert_eq!(session.camera_mode(),"low");}
        if tick==240 {session.set_low_camera(false);assert_eq!(session.camera_mode(),"high");}
        let frame=Instant::now();session.tick(Controls::default()).unwrap();times.push(frame.elapsed().as_secs_f64()*1000.);
        let pose=session.pose();let root=pose.root.w_axis.truncate();
        assert!(root.is_finite()&&pose.velocity.is_finite()&&pose.bones.iter().all(|m|m.is_finite()),"tick {tick}");
        assert!((root.y-spawn[1]).abs()<2.,"floor lost at tick{tick}: {root:?}");
        assert_eq!(pose.tick,tick+1);
        if tick==239 {low_camera=pose.camera;}
        if tick==359 {high_camera=pose.camera;}
    }
    let low=low_camera.expect("original low camera output");let high=high_camera.expect("original high camera output");
    assert!((low.0-high.0).length()>0.01,"original presets should select different camera positions");
    let first=times[0];times[30..].sort_by(f64::total_cmp);let warm=&times[30..];
    eprintln!("COMPACT_SESSION ticks=360 cold_ms={first:.3} warm_p50_ms={:.3} warm_p95_ms={:.3} warm_max_ms={:.3} low={:?} high={:?}",
        warm[warm.len()/2],warm[warm.len()*95/100],warm[warm.len()-1],low.0,high.0);
}

