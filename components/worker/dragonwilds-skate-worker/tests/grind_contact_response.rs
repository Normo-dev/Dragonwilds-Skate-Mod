//! Exact local source support proof; no simulation or game input substitution.
#[path = "../../skate3-mashup/skate/crates/skate-host/src/physics/solve/grind_seams/footprint.rs"]
mod footprint;
#[path = "../../skate3-mashup/skate/crates/skate-host/src/physics/solve/grind_seams/proof.rs"]
mod proof;
#[path = "../../skate3-mashup/skate/crates/skate-host/src/physics/solve/grind_seams/witness.rs"]
mod witness;
use proof::{Frame, core, prove};
use skate_core::physics::{board_world::WorldTriangle, contact::RetailContactMaterial};
fn triangle(a: [f64; 3], b: [f64; 3], c: [f64; 3]) -> WorldTriangle {
    WorldTriangle::from_vertices(
        [a, b, c].map(core),
        RetailContactMaterial {
            static_friction: 0.5,
            dynamic_friction: 0.5,
            restitution: 0.,
        },
        0,
        0,
        [1.; 3],
        0.,
    )
    .unwrap()
}
fn plane(x0: f64, x1: f64, y: f64) -> Vec<WorldTriangle> {
    vec![
        triangle([x0, y, -0.2], [x0, y, 0.2], [x1, y, 0.2]),
        triangle([x0, y, -0.2], [x1, y, 0.2], [x1, y, -0.2]),
    ]
}
fn fixture(gap: f64, height: f64) -> Vec<WorldTriangle> {
    let mut t = plane(-1., -gap, -0.03);
    t.extend(plane(0., 1., height));
    t.extend([
        triangle([0., -0.03, -0.2], [0., -0.03, 0.2], [0., height, 0.2]),
        triangle([0., -0.03, -0.2], [0., height, 0.2], [0., height, -0.2]),
    ]);
    t
}
fn frame() -> Frame {
    Frame {
        start: [-1., 0., 0.],
        end: [1., 0., 0.],
        direction: [1., 0., 0.],
        deck: [-0.3, 0.03, 0.],
        truck: 0.243,
        width: 0.095,
    }
}

#[test]
fn displaced_prism_witness_requires_exact_source_and_collider() {
    use skate_core::physics::{
        board::BodyId, board_step::{BoardCollision, CollisionBody},
        board_world::BoardWorldVolume, collision::WorldContactSettings,
        contact::RetailContactInput,
        world_contact::{ContactPrimitive,primitive_triangle_world_contacts},
    };
    let mut source=triangle([0.,0.,0.],[0.3,0.,-0.5],[0.,0.,-0.5]);
    source.triangle.feature.flags=464;
    source.triangle.feature.edge_cosines=[1.,0.,0.];
    let query=WorldContactSettings{volume_padding:0.05,maximum_separating_distance:0.5,
        edge_cos_bend_normal_threshold:0.999,convexity_epsilon:0.01,is_object:false};
    let volume=BoardWorldVolume{body:CollisionBody::Board(BodyId::Deck),
        primitive:ContactPrimitive::Capsule{center:core([-0.032,0.032,0.12]),
            axis:core([0.,-0.09,0.99594176]),half_length:0.295,radius:0.0075},
        material:source.material,linear_velocity:core([6.6,-0.09,0.33])};
    let manifold=primitive_triangle_world_contacts(volume.primitive,source.triangle,
        volume.linear_velocity,query).unwrap();
    let point=manifold.points[..manifold.count].iter().find(|p|p.b.y>0.002).unwrap();
    let mut row=BoardCollision{body_a:volume.body,body_b:CollisionBody::StaticWorld,
        contact:RetailContactInput{position_on_a:point.a,position_on_b:point.b,
            normal:manifold.normal,restitution:0.,static_friction:0.,dynamic_friction:0.,tag:0}};
    let projected=witness::recovered_point(&row,&volume,query,&source).unwrap();
    assert!(projected[1].abs()<1e-8);
    let mut cache=witness::LeafCache::new();
    for ordinal in 0..witness::MAX_REPLAYS {
        assert_eq!(cache.recovered_point(&row,&volume,ordinal,query,&source),Some(projected));
    }
    assert!(cache.recovered_point(&row,&volume,witness::MAX_REPLAYS,query,&source).is_none());
    // Previously associated rows still resolve after the frame's budget is used.
    assert_eq!(cache.recovered_point(&row,&volume,0,query,&source),Some(projected));
    let mut changed=source;changed.triangle.feature.flags^=8;
    assert!(cache.recovered_point(&row,&volume,0,query,&changed).is_none());
    row.contact.position_on_a.x+=0.001;
    assert!(cache.recovered_point(&row,&volume,0,query,&source).is_none());
    assert!(witness::recovered_point(&row,&volume,query,&source).is_none());
    row.contact.position_on_a=point.a;
    row.contact.position_on_b.y+=0.001;
    assert!(witness::recovered_point(&row,&volume,query,&source).is_none());
    row.contact.position_on_b=point.b;
    let mut other=volume;
    other.body=CollisionBody::Board(BodyId::FrontTruck);
    assert!(witness::recovered_point(&row,&other,query,&source).is_none());
    source.triangle.fatness=0.01;
    assert!(witness::recovered_point(&row,&volume,query,&source).is_none());
}

#[test]
fn high_up_corner_requires_strictly_coplanar_support_and_keeps_pure_floor() {
    let t=plane(-1.,1.,0.);
    let normal=[-0.5,0.866025403784,0.];
    assert!(prove(frame(),[0.,0.,0.],normal,&t).is_none());
    assert!(proof::prove_coplanar_corner(frame(),[0.,0.,0.],normal,&t).is_some());
    assert!(proof::prove_coplanar_corner(frame(),[0.,0.,0.],[0.,1.,0.],&t).is_none());
    assert!(proof::prove_coplanar_corner(frame(),[0.,0.,0.],normal,&fixture(0.,0.)).is_none());
    assert!(proof::prove_coplanar_corner(frame(),[0.,0.,0.],normal,&fixture(0.001,0.)).is_none());
    assert!(proof::prove_coplanar_corner(frame(),[0.,0.,0.],normal,&fixture(0.,0.6)).is_none());
    let mut end=frame();end.end=[0.02,0.,0.];
    assert!(proof::prove_coplanar_corner(end,[0.,0.,0.],normal,&t).is_none());
}
#[test]
fn small_supported_lip_accepts_both_travel_directions() {
    let t = fixture(0., 0.);
    let p = prove(frame(), [0., -0.015, 0.], [-1., 0., 0.], &t).unwrap();
    assert_eq!(p.direction, [1., 0., 0.]);
    assert_eq!(p.up, [0., 1., 0.]);
    assert!((p.low + 0.03).abs() < 1e-8 && p.high.abs() < 1e-8);
    let mut f = frame();
    f.direction = [-1., 0., 0.];
    f.deck = [0.3, 0.03, 0.];
    let reverse: Vec<_> = t
        .iter()
        .map(|t| {
            let p = t
                .triangle
                .vertices
                .map(|p| [-p.x as f64, p.y as f64, p.z as f64]);
            triangle(p[0], p[2], p[1])
        })
        .collect();
    assert!(prove(f, [0., -0.015, 0.], [1., 0., 0.], &reverse).is_some());
}
#[test]
fn positive_gap_is_never_bridged() {
    for gap in [0.0001, 0.002, 0.02] {
        assert!(
            prove(frame(), [0., -0.015, 0.], [-1., 0., 0.], &fixture(gap, 0.)).is_none(),
            "{gap}"
        );
    }
}
#[test]
fn obstacle_above_rail_retains_contact() {
    assert!(prove(frame(), [0., -0.015, 0.], [-1., 0., 0.], &fixture(0., 0.3)).is_none());
}
#[test]
fn buried_join_face_with_coplanar_source_support_is_proved() {
    let mut t = plane(-1., 1., 0.);
    t.extend([
        triangle([0., -0.5, -0.2], [0., -0.5, 0.2], [0., 0., 0.2]),
        triangle([0., -0.5, -0.2], [0., 0., 0.2], [0., 0., -0.2]),
    ]);
    assert!(prove(frame(), [0., -0.015, 0.], [-1., 0., 0.], &t).is_some());
}
#[test]
fn floor_wall_and_rail_end_retain_contact() {
    let t = fixture(0., 0.);
    for n in [[0., 1., 0.], [0., 0., -1.]] {
        assert!(prove(frame(), [0., -0.015, 0.], n, &t).is_none());
    }
    let mut f = frame();
    f.end = [0.02, 0., 0.];
    assert!(prove(f, [0., -0.015, 0.], [-1., 0., 0.], &t).is_none());
}
#[test]
fn yawed_sloped_support_is_proved_in_rail_coordinates() {
    let yaw = 0.7f64;
    let slope = 0.23f64;
    let rotate = |p: [f64; 3]| {
        let x = slope.cos() * p[0] - slope.sin() * p[1];
        let y = slope.sin() * p[0] + slope.cos() * p[1];
        [
            yaw.cos() * x - yaw.sin() * p[2],
            y,
            yaw.sin() * x + yaw.cos() * p[2],
        ]
    };
    let mut f = frame();
    f.start = rotate(f.start);
    f.end = rotate(f.end);
    f.direction = rotate(f.direction);
    f.deck = rotate(f.deck);
    let t: Vec<_> = fixture(0., 0.)
        .iter()
        .map(|t| {
            let p = t
                .triangle
                .vertices
                .map(|p| rotate([p.x as f64, p.y as f64, p.z as f64]));
            triangle(p[0], p[1], p[2])
        })
        .collect();
    assert!(prove(f, rotate([0., -0.015, 0.]), rotate([-1., 0., 0.]), &t).is_some());
}
#[test]
fn invalid_or_remote_support_witness_cannot_expand_board_locality() {
    let zero = [0.; 3];
    let side = [0., 0., 1.];
    assert_eq!(
        footprint::extent([0., 0., 0.3], zero, 0.3, zero, side),
        Some(0.3)
    );
    for point in [[0., 0., 10.], [f64::NAN, 0., 0.], [0., f64::INFINITY, 0.]] {
        assert!(footprint::extent(point, zero, 0.3, zero, side).is_none());
    }
    for radius in [-0.3, f64::NAN, f64::INFINITY] {
        assert!(footprint::extent(zero, zero, radius, zero, side).is_none());
    }
}
#[test]
fn coplanar_native_corner_source_requires_leading_face_at_supported_crown() {
    let face = triangle([0., -1., -0.2], [0., 0., 0.2], [0., 0., -0.2]);
    let f=frame(); let up=[0.,1.,0.]; let travel=[1.,0.,0.];
    assert!(proof::coplanar_source(&face,f.start,up,travel));
    assert!(!proof::coplanar_source(&face,f.start,up,[-1.,0.,0.]));
    let side=triangle([-0.2,-1.,0.],[-0.2,0.,0.],[0.2,0.,0.]);
    assert!(!proof::coplanar_source(&side,f.start,up,travel));
    let tall=triangle([0.,-1.,-0.2],[0.,0.06,0.2],[0.,0.06,-0.2]);
    assert!(!proof::coplanar_source(&tall,f.start,up,travel));
    let normal=[-0.5,0.84,0.2];
    let mut supported=plane(-1.,1.,0.); supported.push(face);
    assert!(proof::prove_coplanar_corner(f,[0.,0.,0.],normal,&supported).is_some());
    let mut gap=plane(-1.,-0.001,0.);gap.extend(plane(0.001,1.,0.));gap.push(face);
    assert!(proof::prove_coplanar_corner(f,[0.,0.,0.],normal,&gap).is_none());
}
