//! Bounded source-contact sweeps over derived object seams; never game input.
//! The real capped provider, truck rectangles and retained 50-50 admission run
//! at every station. A rail count alone cannot establish grind continuity.
#[path="../src/exposed_query.rs"] mod exposed_query;
#[path = "../src/compact_world.rs"]
mod compact_world;
#[path = "../src/exposed_edge_cache.rs"]
mod exposed_edge_cache;
#[path = "../src/exposed_edges.rs"]
mod exposed_edges;
#[path = "../../skate3-mashup/skate/crates/skate-host/src/grind_world/octree.rs"]
mod octree;
#[path = "../../skate3-mashup/skate/crates/skate-host/src/grind_world/spline.rs"]
mod spline;
#[path = "../../skate3-mashup/skate/crates/skate-host/src/grind_world/provider.rs"]
mod provider;

use compact_world::Bounds;
use exposed_edge_cache::{Manifest, Parameters, Snapshot};
use exposed_edges::{Config, Triangle};
use provider::StaticProvider;
use serde_json::{Value, json};
use skate_core::{physics::grind_contact::{self, admission::{Admission, EntryKind}, investigator}, point_graph::PointGraph};
use std::{path::PathBuf, sync::Arc};
type V = [f32; 4];
const TRUCK: f32 = 0.243;
const WHEEL: f32 = 0.09;
const GRAPH: PointGraph<4> = PointGraph { x: [0., 0.3, 0.7, 1.], y: [1.; 4] };

fn cube(lo: [f64; 3], hi: [f64; 3]) -> Vec<Triangle> {
    let points: Vec<[f64; 3]> = (0..8).map(|n| std::array::from_fn(|a| if n & (1 << a) == 0 { lo[a] } else { hi[a] })).collect();
    [[0,4,6],[0,6,2],[1,3,7],[1,7,5],[0,1,5],[0,5,4],[2,6,7],[2,7,3],[0,2,3],[0,3,1],[4,5,7],[4,7,6]]
        .into_iter().map(|t| t.map(|i| points[i])).collect()
}
fn transform(p: [f64; 3], yaw: f64, pitch: f64, offset: [f64; 3]) -> [f64; 3] {
    let (c, s) = (yaw.cos(), yaw.sin());
    let (cp, sp) = (pitch.cos(), pitch.sin());
    let q = [cp * p[0] - sp * p[2], p[1], sp * p[0] + cp * p[2]];
    [c * q[0] - s * q[1] + offset[0], s * q[0] + c * q[1] + offset[1], q[2] + offset[2]]
}
fn source_point(p: [f64; 3]) -> V { [(p[0] * 0.01) as f32, (p[2] * 0.01) as f32, (-p[1] * 0.01) as f32, 0.] }
fn source_direction(p: [f64; 3]) -> V { [p[0] as f32, p[2] as f32, -p[1] as f32, 0.] }
fn overlap(t: &Triangle, b: Bounds) -> bool {
    (0..3).all(|i| t.iter().any(|p| p[i] <= b.max[i]) && t.iter().any(|p| p[i] >= b.min[i]))
}
fn authored(tris: &[Triangle]) -> (StaticProvider, usize) {
    let (provider, count, _) = authored_with_rails(tris);
    (provider, count)
}
fn authored_with_rails(tris: &[Triangle]) -> (StaticProvider, usize, Vec<Vec<[f32;3]>>) {
    let owner = Bounds {
        min: std::array::from_fn(|i| tris.iter().flatten().map(|p| p[i]).fold(f64::INFINITY, f64::min) - 100.),
        max: std::array::from_fn(|i| tris.iter().flatten().map(|p| p[i]).fold(f64::NEG_INFINITY, f64::max) + 100.),
    };
    let patch = exposed_edges::derive(tris, owner, &mut |b| tris.iter().copied().filter(|t| overlap(t,b)).collect(),
        &Config { truck_distance_cm: f64::from(TRUCK) * 100., tolerance_cm: 1e-5 }).unwrap();
    let snapshot = Snapshot {
        manifest: Manifest { magic: "DWE1".into(), schema: 1, complete: true, base_fingerprint: "b".repeat(64),
            scene_fingerprint: None, algorithm_sha256: "a".repeat(64),
            parameters: Parameters { cell_size_cm: 3200., halo_cm: 100., truck_distance_cm: f64::from(TRUCK) * 100., tolerance_cm: 1e-5 },
            cells: vec![], segments: patch.segments.len() },
        path: PathBuf::from("synthetic-unused.json"), manifest_sha256: "c".repeat(64),
        cells: vec![Arc::new(patch.segments)], stats: json!({}),
    };
    let (rails, _) = exposed_edge_cache::rails(&snapshot).unwrap();
    let authored: Vec<_> = rails.iter().cloned().enumerate().map(|(i, points)| skate_data::skate_map::Rail {
        name: format!("synthetic-seam-{i}"), points, closed: false, native: None,
    }).collect();
    let provider = StaticProvider::authored(&authored).unwrap();
    let primitives = provider.primitives().len();
    (provider, primitives, rails)
}

fn retained(provider: &StaticProvider, board: [V; 4], reverse: bool, label: &str) -> usize {
    // Reverse both travel and deck orientation: the source's signed contact
    // comparison must also work after exchanging the front and rear trucks.
    let mut board = board;
    if reverse { board[0] = board[0].map(|v| -v); board[2] = board[2].map(|v| -v); }
    let center = board[3];
    let indices = provider.query(std::array::from_fn(|i| center[i] - 1.2), std::array::from_fn(|i| center[i] + 1.2)).unwrap();
    assert!(indices.len() <= 40);
    let edges: Vec<_> = indices.iter().map(|&i| provider.primitives()[i]).collect();
    let trucks = grind_contact::truck_contacts(board, 0, WHEEL, TRUCK, &edges);
    assert!(trucks.iter().all(Option::is_some), "{label}: truck contact missing, query={}/{} hits={trucks:?}", edges.len(), provider.primitives().len());
    let velocity = board[2].map(|v| v * 3.);
    let query = investigator::Query {
        board, admission: Admission { category: 400, state: 401, speed: 3., velocity, threshold_vs_slope: &GRAPH },
        flags_2468: 0, flags_2472: 0, flags_2476: 0, flags_2484: 0, tip_state: 0,
        truck_to_wheel: WHEEL, deck_to_truck: TRUCK, test_above: 0.04, test_below: 0.04,
        translation: 0., stability_nudge: 0., balance: 0., reference_right: board[0],
        ground_frames: 0, low_wheel_frames: 0, forbidden: false,
    };
    let contact = investigator::investigate(&query, &edges).candidate.unwrap_or_else(|| panic!("{label}: native retained-family admission lost"));
    assert_eq!(contact.kind, 0, "{label}: source changed a retained 50-50 into another family");
    assert_eq!(contact.entry_kind, EntryKind::StayInGrind);
    indices.len()
}

fn run_objects(yaw: f64, pitch: f64, offset: [f64; 3], tiny_shift: f64) {
    let mut tris = Vec::new();
    for index in 0..3 {
        let shift = if index == 1 { tiny_shift } else { 0. };
        let piece = cube([index as f64 * 300. + shift, 0., 0.], [(index+1) as f64 * 300. + shift, 60., 150.]);
        tris.extend(piece.into_iter().map(|t| t.map(|p| transform(p,yaw,pitch,offset))));
    }
    let (provider, primitives) = authored(&tris);
    let right = source_direction(transform([0.,1.,0.], yaw, pitch, [0.;3]));
    let up = source_direction(transform([0.,0.,1.], yaw, pitch, [0.;3]));
    let forward = source_direction(transform([1.,0.,0.], yaw, pitch, [0.;3]));
    let mut stations: Vec<f64> = (50..=850).step_by(5).map(f64::from).collect();
    for seam in [300.,600.] { for truck_offset in [-f64::from(TRUCK)*100.,0.,f64::from(TRUCK)*100.] {
        for epsilon in [-0.001,0.,0.001] { stations.push(seam+truck_offset+epsilon); }
    }}
    let mut max_query = 0;
    for reverse in [false,true] { for &station in &stations {
        let at = source_point(transform([station,0.,156.], yaw,pitch,offset));
        let label = format!("yaw={yaw} pitch={pitch} station={station} reverse={reverse} tiny_shift={tiny_shift}");
        max_query = max_query.max(retained(&provider,[right,up,forward,at],reverse,&label));
    }}
    eprintln!("SEAM_CONTACT_SWEEP stations={} primitives={primitives} maximum_native_query={max_query} yaw={yaw} pitch={pitch}",stations.len()*2);
}

#[test]
fn aligned_adjacent_objects_retain_contacts_in_both_directions() { run_objects(0.,0.,[0.;3],0.); }
#[test]
fn rotated_sloped_objects_retain_contacts_at_distant_coordinates() {
    run_objects(37f64.to_radians(),17f64.to_radians(),[16255.,167450.,-8630.],0.);
}
#[test]
fn tiny_transform_roundoff_does_not_force_an_object_seam_exit() {
    run_objects(23f64.to_radians(),11f64.to_radians(),[16255.,167450.,-8630.],1e-7);
}

fn run_stepped_frames(yaw: f64, pitch: f64, offset: [f64; 3]) {
    // Three collision boxes per frame, with the recorded Castle post/lintel
    // height difference. The shape, rather than its mesh name, supplies support.
    let mut tris = vec![];
    for i in 0..6 {
        let x = i as f64 * 300.;
        tris.extend(cube([x - 150., -23.463154, 243.155802], [x + 150., 25.69247, 296.844198]));
        for center in [x - 140., x + 140.] {
            tris.extend(cube([center - 13.5498465, -23.463154, 0.], [center + 13.5498465, 25.69247, 300.]));
        }
    }
    let tris: Vec<_> = tris.into_iter().map(|t| t.map(|p| transform(p, yaw, pitch, offset))).collect();
    let (provider, primitives) = authored(&tris);
    let right = source_direction(transform([0.,1.,0.], yaw, pitch, [0.;3]));
    let up = source_direction(transform([0.,0.,1.], yaw, pitch, [0.;3]));
    let forward = source_direction(transform([1.,0.,0.], yaw, pitch, [0.;3]));
    let mut stations: Vec<f64> = (-90..=1590).step_by(5).map(f64::from).collect();
    // Land each truck exactly on, and immediately around, every object's
    // meeting plane and both post/lintel boundaries.
    for frame in 0..6 { for local in [-150., -126.4501535, 126.4501535, 150.] {
        for truck in [-f64::from(TRUCK)*100.,0.,f64::from(TRUCK)*100.] {
            for epsilon in [-0.001,0.,0.001] {
                let x = frame as f64 * 300. + local + truck + epsilon;
                if (-90. ..=1590.).contains(&x) { stations.push(x); }
            }
        }
    }}
    let mut max_query = 0;
    for side in [-23.463154,25.69247] { for reverse in [false,true] { for &station in &stations {
        let at = source_point(transform([station,side,306.], yaw,pitch,offset));
        max_query = max_query.max(retained(&provider,[right,up,forward,at],reverse,
            &format!("Castle shape yaw={yaw} pitch={pitch} side={side} x={station} reverse={reverse}")));
    }}}
    eprintln!("CASTLE_SHAPE_CONTACT_SWEEP stations={} primitives={primitives} maximum_native_query={max_query}", stations.len()*4);
}

#[test]
fn stepped_castle_shape_retains_both_outer_contacts_at_every_post_and_object_seam() {
    run_stepped_frames(0.,0.,[0.;3]);
    run_stepped_frames(37f64.to_radians(),17f64.to_radians(),[16255.,167450.,-8630.]);
}

#[test]
fn connected_short_curve_segments_retain_native_contacts_in_both_directions() {
    // The original short-crown geometry regression checked only rail counts.
    // Use a larger curved crown so both native truck rectangles fit on each
    // side of every joint; preserve many sub-50cm component segments.
    let radius = 400.;
    let count = 24;
    let points: Vec<[f64;3]> = (0..=count).map(|i| {
        let angle = i as f64 * std::f64::consts::FRAC_PI_2 / count as f64;
        [radius*angle.cos(),radius*angle.sin(),40.]
    }).collect();
    let mut tris = vec![];
    for i in 0..count {
        let p = points[i]; let q = points[i+1];
        let inner = |p:[f64;3]| [p[0]*(radius-20.)/radius,p[1]*(radius-20.)/radius,0.];
        let outer = |p:[f64;3]| [p[0]*(radius+20.)/radius,p[1]*(radius+20.)/radius,0.];
        let (pi,qi,po,qo)=(inner(p),inner(q),outer(p),outer(q));
        tris.extend([[pi,q,qi],[pi,p,q],[p,qo,q],[p,po,qo]]);
    }
    for (yaw,pitch,offset) in [(0.,0.,[0.;3]),(0.64,0.19,[16255.,167450.,-8630.])] {
        let tris: Vec<_> = tris.iter().map(|t| t.map(|p|transform(p,yaw,pitch,offset))).collect();
        let (provider,_) = authored(&tris);
        for i in 1..count-1 { for fraction in [0.,0.00001,0.25,0.5,0.75,0.99999,1.] {
            let p = points[i]; let q = points[i+1];
            let d:[f64;3]=std::array::from_fn(|a|q[a]-p[a]);
            let len=d.iter().map(|v|v*v).sum::<f64>().sqrt();
            let forward=[d[0]/len,d[1]/len,0.];
            let right=[-forward[1],forward[0],0.];
            let at=std::array::from_fn(|a|p[a]+d[a]*fraction+if a==2 {6.} else {0.});
            let board=[source_direction(transform(right,yaw,pitch,[0.;3])),
                source_direction(transform([0.,0.,1.],yaw,pitch,[0.;3])),
                source_direction(transform(forward,yaw,pitch,[0.;3])),source_point(transform(at,yaw,pitch,offset))];
            for reverse in [false,true] {
                retained(&provider,board,reverse,&format!("curve joint={i} fraction={fraction} reverse={reverse} pitch={pitch}"));
            }
        }}
    }
}

#[test]
fn source_trucks_lose_contact_at_a_real_hole_and_tall_obstacle() {
    let mut hole = cube([-300.,0.,0.],[-50.,60.,150.]);
    hole.extend(cube([50.,0.,0.],[300.,60.,150.]));
    let mut obstacle = cube([-300.,0.,0.],[300.,60.,150.]);
    obstacle.extend(cube([-50.,-20.,150.],[50.,20.,230.]));
    for (name,tris) in [("hole",hole),("obstacle",obstacle)] {
        let (provider,_) = authored(&tris);
        let board=[[0.,0.,-1.,0.],[0.,1.,0.,0.],[1.,0.,0.,0.],[0.,1.56,0.,0.]];
        let center=board[3];
        let indices=provider.query(std::array::from_fn(|i|center[i]-1.2),std::array::from_fn(|i|center[i]+1.2)).unwrap();
        let edges:Vec<_>=indices.iter().map(|&i|provider.primitives()[i]).collect();
        let contacts=grind_contact::truck_contacts(board,0,WHEEL,TRUCK,&edges);
        assert!(contacts.iter().all(Option::is_none),"{name} must interrupt actual truck support: {contacts:?}");
        assert!(grind_contact::fifty_fifty_candidate_on_splines(board,[0.;2],contacts,&edges).is_none(),
            "{name} must not retain source 50-50 admission");
    }
}

#[test]
fn different_spline_owners_do_not_force_native_family_exit() {
    let provider = StaticProvider::authored(&[
        skate_data::skate_map::Rail { name:"first-object".into(),points:vec![[-3.,0.,0.],[0.,0.,0.]],closed:false,native:None },
        skate_data::skate_map::Rail { name:"second-object".into(),points:vec![[0.,0.,0.],[3.,0.,0.]],closed:false,native:None },
    ]).unwrap();
    assert_ne!(provider.primitives()[0].owner,provider.primitives()[1].owner);
    for station in [-0.243001f32,-0.243,-0.242999,-0.000001,0.,0.000001,0.242999,0.243,0.243001] {
        retained(&provider,[[0.,0.,-1.,0.],[0.,1.,0.,0.],[1.,0.,0.,0.],[station,0.06,0.,0.]],false,"distinct owners");
    }
}

#[test]
#[ignore="requires external caller-owned six-Castle fixture; no game assets bundled"]
fn recorded_castle_run_uses_derived_rails_and_retains_source_contacts() {
    let path = std::env::var("SKATE_DOORFRAME_FIXTURE").expect("Provide external owned doorway fixture");
    let value: Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    let local: Vec<Triangle> = serde_json::from_value(value["triangles_cm"].clone()).unwrap();
    let offsets: Vec<[f64;3]> = value["frames"].as_array().unwrap().iter().map(|v| {
        let t=&v["transform"]["Translation"]; [t["X"].as_f64().unwrap(),t["Y"].as_f64().unwrap(),t["Z"].as_f64().unwrap()]
    }).collect();
    let tris: Vec<_> = offsets.iter().flat_map(|offset| local.iter().map(move |t| t.map(|p| std::array::from_fn(|i|p[i]+offset[i])))).collect();
    let (provider, _, rails) = authored_with_rails(&tris);
    if let Ok(path) = std::env::var("SKATE_DOORFRAME_RAIL_OUTPUT") {
        std::fs::write(path,serde_json::to_vec_pretty(&json!({"private_owned_fixture":true,
            "rails_m":rails,"origin_cm":offsets[0],"triangles_cm":tris})).unwrap()).unwrap();
    }
    let ymin = local.iter().flatten().map(|p|p[1]).fold(f64::INFINITY,f64::min)+offsets[0][1];
    let ymax = local.iter().flatten().map(|p|p[1]).fold(f64::NEG_INFINITY,f64::max)+offsets[0][1];
    let top = local.iter().flatten().map(|p|p[2]).fold(f64::NEG_INFINITY,f64::max)+offsets[0][2];
    let x0 = tris.iter().flatten().map(|p|p[0]).fold(f64::INFINITY,f64::min)+50.;
    let x1 = tris.iter().flatten().map(|p|p[0]).fold(f64::NEG_INFINITY,f64::max)-50.;
    let mut stations=vec![]; let mut x=x0;
    while x<=x1 { stations.push(x); x+=5.; }
    for t in &local { for p in t { for offset in &offsets {
        for truck in [-f64::from(TRUCK)*100.,0.,f64::from(TRUCK)*100.] {
            for epsilon in [-0.001,0.,0.001] {
                let x=p[0]+offset[0]+truck+epsilon;
                if (x0..=x1).contains(&x) { stations.push(x); }
            }
        }
    }}}
    stations.sort_by(f64::total_cmp); stations.dedup();
    for side in [ymin,ymax] { for reverse in [false,true] { for &x in &stations {
        let point=source_point([x,side,top+6.]);
        retained(&provider,[[0.,0.,-1.,0.],[0.,1.,0.,0.],[1.,0.,0.,0.],point],reverse,
            &format!("private Castle station {x} side={side} reverse={reverse}"));
    }}}
    eprintln!("PRIVATE_CASTLE_CONTACT_SWEEP stations={}",stations.len()*4);
}
