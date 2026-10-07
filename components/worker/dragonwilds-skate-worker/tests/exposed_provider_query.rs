//! Exposed-only host admission; the original contact routines remain the oracle.
#[path = "../../skate3-mashup/skate/crates/skate-host/src/grind_world/octree.rs"]
mod octree;
#[path = "../../skate3-mashup/skate/crates/skate-host/src/grind_world/provider.rs"]
mod provider;
#[path = "../../skate3-mashup/skate/crates/skate-host/src/grind_world/spline.rs"]
mod spline;
use provider::StaticProvider;
use skate_core::physics::grind_contact::{self, Primitive};
use skate_data::skate_map::Rail;

fn rail(i: usize, a: [f32; 3], b: [f32; 3]) -> Rail {
    Rail {
        name: format!("synthetic-{i}"),
        points: vec![a, b],
        closed: false,
        native: None,
    }
}
fn full_order(provider: &StaticProvider, min: [f32; 3], max: [f32; 3]) -> Vec<usize> {
    let boxes: Vec<_> = provider
        .primitives()
        .iter()
        .map(|p| octree::Bounds {
            min: std::array::from_fn(|i| p.start[i].min(p.end[i])),
            max: std::array::from_fn(|i| p.start[i].max(p.end[i])),
        })
        .collect();
    let all = boxes
        .iter()
        .copied()
        .reduce(octree::Bounds::union)
        .unwrap()
        .padded();
    octree::Octree::new(all, boxes)
        .unwrap()
        .query(octree::Bounds { min, max }, usize::MAX)
}
#[test]
fn visitor_preserves_native_head_first_and_early_stop() {
    let b = octree::Bounds {
        min: [-2.; 3],
        max: [2.; 3],
    };
    let tree = octree::Octree::new(
        octree::Bounds {
            min: [-10.; 3],
            max: [10.; 3],
        },
        vec![b; 73],
    )
    .unwrap();
    let expected: Vec<_> = (0..73).rev().collect();
    let mut seen = vec![];
    tree.visit(b, |i| {
        seen.push(i);
        true
    });
    assert_eq!(seen, expected);
    assert_eq!(tree.query(b, 40), expected[..40]);
    let mut short = vec![];
    tree.visit(b, |i| {
        short.push(i);
        short.len() < 7
    });
    assert_eq!(short, expected[..7]);
    assert!(tree.query(b, 0).is_empty());
}
#[test]
fn empty_and_low_density_opt_in_preserve_exact_results() {
    let mut empty = StaticProvider::new(None).unwrap();
    empty.enable_dense_authored().unwrap();
    assert!(empty.query([-1.; 3], [1.; 3]).unwrap().is_empty());
    for count in [1, 7, 39, 40] {
        let rails: Vec<_> = (0..count)
            .map(|i| {
                let x = (i * 17 % 31) as f32 * 0.1 - 1.5;
                let y = (i * 11 % 23) as f32 * 0.1 - 1.;
                rail(i, [x, y, -1.], [x + 0.2, y, 1.])
            })
            .collect();
        let legacy = StaticProvider::authored(&rails).unwrap();
        let mut dense = StaticProvider::authored(&rails).unwrap();
        dense.enable_dense_authored().unwrap();
        for x in [-1., 0., 1.] {
            let lo = [x - 1.2, -2., -2.];
            let hi = [x + 1.2, 2., 2.];
            assert_eq!(dense.query(lo, hi).unwrap(), legacy.query(lo, hi).unwrap());
        }
        assert_eq!(
            dense.query([0.; 3], [0.; 3]).unwrap(),
            legacy.query([0.; 3], [0.; 3]).unwrap()
        );
    }
}
#[test]
fn dense_ties_use_primitive_id_and_restore_native_order() {
    let rails: Vec<_> = (0..64)
        .map(|i| rail(i, [-1., 0., 0.], [1., 0., 0.]))
        .collect();
    let legacy = StaticProvider::authored(&rails).unwrap();
    let mut dense = StaticProvider::authored(&rails).unwrap();
    dense.enable_dense_authored().unwrap();
    let lo = [-2.; 3];
    let hi = [2.; 3];
    let all = full_order(&legacy, lo, hi);
    assert_eq!(legacy.query(lo, hi).unwrap(), all[..40]);
    let expected: Vec<_> = all.into_iter().filter(|i| *i < 40).collect();
    assert_ne!(expected, legacy.query(lo, hi).unwrap());
    for _ in 0..10 {
        assert_eq!(dense.query(lo, hi).unwrap(), expected);
    }
    // Opting in a separate exposed provider never modifies the legacy one.
    assert_eq!(
        legacy.query(lo, hi).unwrap(),
        (24..64).rev().collect::<Vec<_>>()
    );
}
#[test]
fn closest_chord_not_midpoint_and_large_finite_distances() {
    let mut rails = vec![rail(0, [-1., 0., 0.], [10., 0., 0.])];
    rails.extend((1..65).map(|i| rail(i, [-0.1, 0.5, 0.], [0.1, 0.5, 0.])));
    let mut dense = StaticProvider::authored(&rails).unwrap();
    dense.enable_dense_authored().unwrap();
    let mut ids = dense.query([-1.2; 3], [1.2; 3]).unwrap();
    ids.sort_unstable();
    assert_eq!(ids, (0..40).collect::<Vec<_>>());
    let rails: Vec<_> = (0..65)
        .map(|i| {
            let x = 1e20f32 * (1. + i as f32 * 0.01);
            rail(i, [x, 0., -1.], [x, 0., 1.])
        })
        .collect();
    let mut dense = StaticProvider::authored(&rails).unwrap();
    dense.enable_dense_authored().unwrap();
    let mut ids = dense.query([-f32::MAX; 3], [f32::MAX; 3]).unwrap();
    ids.sort_unstable();
    assert_eq!(ids, (0..40).collect::<Vec<_>>());
}
#[test]
fn crowded_near_ledge_recovers_original_truck_and_retained_family_contacts() {
    let mut rails = vec![rail(0, [0., 0., -1.], [0., 0., 1.])];
    rails.extend((1..81).map(|i| rail(i, [0.6, 0., -1.], [0.6, 0., 1.])));
    let legacy = StaticProvider::authored(&rails).unwrap();
    let mut dense = StaticProvider::authored(&rails).unwrap();
    dense.enable_dense_authored().unwrap();
    let board = [
        [1., 0., 0., 0.],
        [0., 1., 0., 0.],
        [0., 0., 1., 0.],
        [0., 0.06, 0., 0.],
    ];
    let lo = [-1.2, -1.14, -1.2];
    let hi = [1.2, 1.26, 1.2];
    let contact = |primitives: &[Primitive]| {
        let hits = grind_contact::truck_contacts(board, 0, 0.09, 0.243, primitives);
        grind_contact::fifty_fifty_candidate_on_splines(board, [0.; 2], hits, primitives)
    };
    assert!(contact(legacy.primitives()).is_some());
    let old = legacy.query(lo, hi).unwrap();
    assert_eq!(old.len(), 40);
    assert!(!old.contains(&0));
    assert!(
        contact(
            &old.iter()
                .map(|&i| legacy.primitives()[i])
                .collect::<Vec<_>>()
        )
        .is_none()
    );
    let selected = dense.query(lo, hi).unwrap();
    assert_eq!(selected.len(), 40);
    assert!(selected.contains(&0));
    let expected_order: Vec<_> = full_order(&dense, lo, hi)
        .into_iter()
        .filter(|i| selected.contains(i))
        .collect();
    assert_eq!(selected, expected_order);
    assert!(
        contact(
            &selected
                .iter()
                .map(|&i| dense.primitives()[i])
                .collect::<Vec<_>>()
        )
        .is_some()
    );
}
