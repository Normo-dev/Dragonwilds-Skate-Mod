mod compact_world {
    #[derive(Clone, Copy, Debug)]
    pub struct Bounds {
        pub min: [f64; 3],
        pub max: [f64; 3],
    }
}
#[path = "../src/exposed_edges.rs"]
mod exposed_edges;
use compact_world::Bounds;
use exposed_edges::{Config, Patch, Triangle, derive};
fn box_triangles(lo: [f64; 3], hi: [f64; 3]) -> Vec<Triangle> {
    let p: Vec<[f64; 3]> = (0..8)
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
    .map(|t| t.map(|i| p[i]))
    .collect()
}
fn owner() -> Bounds {
    Bounds {
        min: [-1000.; 3],
        max: [1000.; 3],
    }
}
fn overlap(tri: &Triangle, b: Bounds) -> bool {
    (0..3).all(|i| tri.iter().any(|p| p[i] <= b.max[i]) && tri.iter().any(|p| p[i] >= b.min[i]))
}
fn patch(tris: &[Triangle]) -> Patch {
    derive(
        tris,
        owner(),
        &mut |b| tris.iter().copied().filter(|t| overlap(t, b)).collect(),
        &Config::default(),
    )
    .unwrap()
}
fn rotated(tris: &[Triangle], yaw: f64, pitch: f64) -> Vec<Triangle> {
    let (c, s) = (yaw.cos(), yaw.sin());
    let (cp, sp) = (pitch.cos(), pitch.sin());
    tris.iter()
        .map(|t| {
            t.map(|p| {
                let q = [cp * p[0] + sp * p[2], p[1], -sp * p[0] + cp * p[2]];
                [c * q[0] - s * q[1], s * q[0] + c * q[1], q[2]]
            })
        })
        .collect()
}
#[test]
fn short_exposed_tops_survive_and_undersides_do_not() {
    let tris = box_triangles([-19., -25., 0.], [19., 25., 300.]);
    let p = patch(&tris);
    assert_eq!(p.segments.len(), 4, "{:?}", p.census);
    assert!(p.segments.iter().all(|s| s.a[2] == 300. && s.b[2] == 300.));
    assert!(p.segments.iter().all(|s| {
        s.a.iter()
            .zip(s.b)
            .map(|(a, b)| (a - b).powi(2))
            .sum::<f64>()
            .sqrt()
            < 60.96
    }));
}
#[test]
fn inward_closed_winding_and_yaw_have_the_same_physical_outline() {
    let tris = box_triangles([-100., -25., 0.], [100., 25., 100.]);
    let original = patch(&tris);
    let inward: Vec<_> = tris.iter().map(|t| [t[0], t[2], t[1]]).collect();
    let reverse = patch(&inward);
    assert_eq!(original.segments, reverse.segments);
    assert_eq!(reverse.census.closed_components_reoriented, 1);
    for yaw in [0.1, 0.645771823, 1.57079632679] {
        let r = patch(&rotated(&tris, yaw, 0.));
        assert_eq!(r.segments.len(), 4, "yaw{yaw}: {:?}", r.census);
    }
}
#[test]
fn diagonal_beam_is_not_lost_at_the_old_44_degree_limit() {
    let tris = rotated(
        &box_triangles([-150., -12., -12.], [150., 12., 12.]),
        0.73,
        -std::f64::consts::FRAC_PI_4,
    );
    let p = patch(&tris);
    assert!(
        p.segments.iter().any(|s| {
            let d: Vec<_> = s.a.iter().zip(s.b).map(|(a, b)| b - a).collect();
            let n = d.iter().map(|x| x * x).sum::<f64>().sqrt();
            n > 200. && d[2].abs() / n > 0.7
        }),
        "{:?}",
        p.census
    );
}
#[test]
fn coplanar_diagonals_and_touching_box_seams_are_not_rails() {
    let mut tris = box_triangles([-100., -100., -20.], [0., 100., 0.]);
    tris.extend(box_triangles([0., -100., -20.], [100., 100., 0.]));
    let p = patch(&tris);
    assert!(p.census.coplanar_edges > 0);
    assert!(
        !p.segments
            .iter()
            .any(|s| s.a[0].abs() < 1e-6 && s.b[0].abs() < 1e-6),
        "{:?}",
        p.segments
    );
    assert!(p.segments.iter().all(|s| s.a[2] == 0. && s.b[2] == 0.));
}
#[test]
fn steep_bevel_on_a_broad_surface_does_not_expose_its_gentle_internal_diagonal() {
    // The top diagonal is a two-centimetre gentle ridge on a two-metre face.
    // Steep bevels belong to the same connected surface and enter the diagonal's
    // query at its ends; they must not grant the narrow round-crown exception.
    let tris: Vec<_> = box_triangles([-100., -100., 0.], [100., 100., 100.])
        .into_iter()
        .map(|t| {
            t.map(|mut p| {
                if p[2] == 0. {
                    p[0] *= 1.5;
                    p[1] *= 1.5;
                } else if p[0] != p[1] {
                    p[2] -= 2.;
                }
                p
            })
        })
        .collect();
    let p = patch(&tris);
    assert!(p.census.smooth_support_seams > 0, "{:?}", p.census);
    assert!(
        !p.segments.iter().any(|s| {
            [s.a, s.b]
                .into_iter()
                .all(|v| (v[0] - v[1]).abs() < 1e-5 && v[2] > 99.)
        }),
        "internal diagonal leaked: {:?}",
        p.segments
    );
    assert!(!p.segments.is_empty(), "the exposed outline must remain");
}
#[test]
fn a_real_gap_is_not_bridged_and_obstacles_split_continuously() {
    let mut tris = box_triangles([-150., -30., -30.], [-2., 30., 0.]);
    tris.extend(box_triangles([2., -30., -30.], [150., 30., 0.]));
    let p = patch(&tris);
    assert!(p.segments.iter().all(|s| !(s.a[0] < -2. && s.b[0] > 2.)));
    let mut full = box_triangles([-150., -30., -30.], [150., 30., 0.]);
    let candidates = full.clone();
    full.extend(box_triangles([-7., -45., 0.], [7., -25., 35.]));
    let p = derive(
        &candidates,
        owner(),
        &mut |b| full.iter().copied().filter(|t| overlap(t, b)).collect(),
        &Config::default(),
    )
    .unwrap();
    let edge: Vec<_> = p
        .segments
        .iter()
        .filter(|s| (s.a[1] + 30.).abs() < 1e-6 && (s.b[1] + 30.).abs() < 1e-6)
        .collect();
    assert_eq!(edge.len(), 2);
    assert!(edge.iter().all(|s| s.b[0] < -7. || s.a[0] > 7.));
}
#[test]
fn cell_boundaries_are_owned_once_and_dependency_boxes_cover_probes() {
    let tris = box_triangles([0., -30., -30.], [100., 30., 0.]);
    let mut total = 0;
    for (lo, hi) in [(-100., 0.), (0., 100.), (100., 200.)] {
        let owner = Bounds {
            min: [lo, -100., -100.],
            max: [hi, 100., 100.],
        };
        let p = derive(
            &tris,
            owner,
            &mut |b| tris.iter().copied().filter(|t| overlap(t, b)).collect(),
            &Config::default(),
        )
        .unwrap();
        total += p.segments.len();
        assert!(
            p.dependencies
                .iter()
                .all(|b| b.max[2] > 25. && b.min[2] < -25.)
        );
    }
    assert_eq!(total, 4);
}
#[test]
fn tilted_ridge_retains_a_connected_curve_without_vertical_corners() {
    // A closed roof with two sloped faces and a segmented horizontal crown.
    let mut tris = vec![];
    for i in 0..8 {
        let a = i as f64 * std::f64::consts::PI / 16.;
        let b = (i + 1) as f64 * std::f64::consts::PI / 16.;
        let p = [100. * a.cos(), 100. * a.sin(), 40.];
        let q = [100. * b.cos(), 100. * b.sin(), 40.];
        let pi = [80. * a.cos(), 80. * a.sin(), 0.];
        let qi = [80. * b.cos(), 80. * b.sin(), 0.];
        let po = [120. * a.cos(), 120. * a.sin(), 0.];
        let qo = [120. * b.cos(), 120. * b.sin(), 0.];
        tris.extend([[pi, q, qi], [pi, p, q], [p, qo, q], [p, po, qo]]);
    }
    let p = patch(&tris);
    let crown: Vec<_> = p
        .segments
        .iter()
        .filter(|s| (s.a[2] - 40.).abs() < 1e-6 && (s.b[2] - 40.).abs() < 1e-6)
        .collect();
    assert_eq!(crown.len(), 8, "{:?}", p.census);
    assert!(crown.iter().all(|s| {
        s.a.iter()
            .zip(s.b)
            .map(|(a, b)| (a - b).powi(2))
            .sum::<f64>()
            .sqrt()
            < 60.96
    }));
}
#[test]
fn invalid_config_or_queries_never_silently_emit_rails() {
    let tris = box_triangles([-20.; 3], [20.; 3]);
    let mut c = Config::default();
    c.truck_distance_cm = f64::NAN;
    assert!(derive(&tris, owner(), &mut |_| tris.clone(), &c).is_err());
    assert!(
        derive(
            &tris,
            owner(),
            &mut |_| vec![[[f64::NAN; 3]; 3]],
            &Config::default()
        )
        .is_err()
    );
}

#[test]
fn distant_rotated_sloped_cell_cuts_share_exact_endpoint_bits() {
    let source = rotated(
        &box_triangles([-250., -25., -25.], [250., 25., 25.]),
        0.645771823,
        -0.48,
    );
    let origin = [320_000., -640_000., 120_000.];
    let tris: Vec<_> = source
        .into_iter()
        .map(|t| t.map(|p| std::array::from_fn(|i| p[i] + origin[i])))
        .collect();
    let make = |lo, hi| {
        derive(
            &tris,
            Bounds {
                min: [lo, origin[1] - 1000., origin[2] - 1000.],
                max: [hi, origin[1] + 1000., origin[2] + 1000.],
            },
            &mut |b| tris.iter().copied().filter(|t| overlap(t, b)).collect(),
            &Config::default(),
        )
        .unwrap()
    };
    let left = make(origin[0] - 1000., origin[0]);
    let right = make(origin[0], origin[0] + 1000.);
    let cuts = |p: &Patch| {
        let mut result: Vec<_> = p
            .segments
            .iter()
            .flat_map(|s| [s.a, s.b])
            .filter(|p| p[0] == origin[0])
            .map(|p| p.map(f64::to_bits))
            .collect();
        result.sort();
        result
    };
    assert!(!cuts(&left).is_empty());
    assert_eq!(cuts(&left), cuts(&right));
    let whole = make(origin[0] - 1000., origin[0] + 1000.);
    for endpoint in whole.segments.iter().flat_map(|s| [s.a, s.b]) {
        assert!(
            tris.iter()
                .flatten()
                .any(|v| v.map(f64::to_bits) == endpoint.map(f64::to_bits)),
            "Uncut endpoints must preserve collision vertices"
        );
    }
}

#[test]
fn gentle_terrain_facets_do_not_become_internal_rails() {
    let mut tris = vec![];
    for x in 0..20 {
        for y in 0..20 {
            let vertex = |i: usize, j: usize| {
                [
                    i as f64 * 10.,
                    j as f64 * 10.,
                    ((i * 13 + j * 7) as f64).sin() * 0.01,
                ]
            };
            let (a, b, c, d) = (
                vertex(x, y),
                vertex(x + 1, y),
                vertex(x + 1, y + 1),
                vertex(x, y + 1),
            );
            tris.extend([[a, b, c], [a, c, d]]);
        }
    }
    let p = patch(&tris);
    assert!(p.census.candidate_edges > 100);
    assert!(!p.segments.is_empty());
    assert!(
        p.segments.iter().all(|s| (0..2)
            .any(|i| (s.a[i] == 0. && s.b[i] == 0.) || (s.a[i] == 200. && s.b[i] == 200.))),
        "Interior terrain facets were emitted: {:?}",
        p.census
    );
}

fn cylinder(sides: usize, offset: [f64; 3]) -> Vec<Triangle> {
    let mut tris = vec![];
    let p = |x: f64, i: usize| {
        let angle = std::f64::consts::TAU * (i % sides) as f64 / sides as f64;
        [
            x + offset[0],
            5. * angle.cos() + offset[1],
            5. * angle.sin() + offset[2],
        ]
    };
    for i in 0..sides {
        let (a, b, c, d) = (p(-150., i), p(150., i), p(150., i + 1), p(-150., i + 1));
        tris.extend([
            [a, b, c],
            [a, c, d],
            [[offset[0] - 150., offset[1], offset[2]], a, d],
            [[offset[0] + 150., offset[1], offset[2]], c, b],
        ]);
    }
    tris
}
#[test]
fn round_rail_crowns_do_not_emit_every_upper_facet_or_hide_separate_rails() {
    for count in [16, 32, 64] {
        let tris = cylinder(count, [0.; 3]);
        let p = patch(&tris);
        let middle: Vec<_> = p
            .segments
            .iter()
            .filter(|s| s.a[0] < -100. && s.b[0] > 100.)
            .collect();
        assert_eq!(
            middle.len(),
            1,
            "{count} facets: {:?} / {:?}",
            p.census,
            middle
        );
        assert!(middle.iter().all(|s| s.a[2] > 4.99 && s.b[2] > 4.99));
        let mut two = tris;
        two.extend(cylinder(count, [0., 13., 2.]));
        let p = patch(&two);
        let middle: Vec<_> = p
            .segments
            .iter()
            .filter(|s| s.a[0] < -100. && s.b[0] > 100.)
            .collect();
        assert_eq!(
            middle.len(),
            2,
            "Nearby separate rail suppressed: {count} {:?}",
            p.census
        );
    }
}

#[test]
fn rounded_crown_wider_than_near_probe_spacing_is_not_a_flat_seam() {
    for sides in [16, 32, 64] {
        let tris: Vec<_> = cylinder(sides, [0.; 3])
            .into_iter()
            .map(|t| t.map(|p| [p[0], p[1] * 2., p[2] * 2.]))
            .collect();
        let p = patch(&tris);
        let middle: Vec<_> = p
            .segments
            .iter()
            .filter(|s| s.a[0] < -100. && s.b[0] > 100.)
            .collect();
        assert_eq!(
            middle.len(),
            1,
            "radius10/{sides}: {:?} {:?}",
            p.census,
            middle
        );
        assert!(middle[0].a[2] > 9.99 && middle[0].b[2] > 9.99);
    }
}

#[test]
fn raised_capsule_endpoint_obstacle_is_included_in_dependency_query() {
    let input = box_triangles([-100., -20., -30.], [100., 20., 0.]);
    let mut world = input.clone();
    world.extend(box_triangles([100.05, -22., 8.], [101., -18., 10.]));
    let p = derive(
        &input,
        owner(),
        &mut |b| world.iter().copied().filter(|t| overlap(t, b)).collect(),
        &Config::default(),
    )
    .unwrap();
    let front: Vec<_> = p
        .segments
        .iter()
        .filter(|s| s.a[1] == -20. && s.b[1] == -20. && s.a[0] < 0.)
        .collect();
    assert_eq!(front.len(), 1);
    assert!(
        front[0].b[0] < 99.96,
        "Endpoint capsule obstacle was missed: {:?}",
        front
    );
    assert!(p.dependencies.iter().any(|b| b.max[0] > 100.1));
}

#[test]
fn edges_buried_in_a_proved_convex_solid_are_not_exposed_rails() {
    let small = box_triangles([-20., -20., -20.], [20., 20., 0.]);
    let large = box_triangles([-100.; 3], [100.; 3]);
    let mut world = large.clone();
    world.extend(small.clone());
    let p = patch(&world);
    assert_eq!(p.census.proved_convex_solids, 2);
    assert!(p.census.buried_edge_spans >= 4);
    assert_eq!(p.segments.len(), 4, "{:?}", p.segments);
    assert!(p.segments.iter().all(|s| s.a[2] == 100. && s.b[2] == 100.));
    // Inward one-sided geometry may be an accessible container. Authoring
    // normal normalization alone must not declare its interior filled.
    let mut hollow: Vec<_> = large.iter().map(|t| [t[0], t[2], t[1]]).collect();
    hollow.extend(small);
    let p = patch(&hollow);
    assert_eq!(p.census.proved_convex_solids, 1);
    assert_eq!(
        p.segments
            .iter()
            .filter(|s| s.a[2] == 0. && s.b[2] == 0.)
            .count(),
        4
    );
}

#[test]
fn narrow_nonconforming_flat_t_junctions_have_only_outer_edges() {
    let tris = vec![
        [[-4., 0., 0.], [0., 0., 0.], [0., 100., 0.]],
        [[-4., 0., 0.], [0., 100., 0.], [-4., 100., 0.]],
        [[0., 0., 0.], [4., 0., 0.], [0., 50., 0.]],
        [[4., 0., 0.], [4., 100., 0.], [0., 50., 0.]],
        [[0., 50., 0.], [4., 100., 0.], [0., 100., 0.]],
    ];
    let p = patch(&tris);
    assert!(
        !p.segments.iter().any(|s| s.a[0] == 0. && s.b[0] == 0.),
        "{:?}",
        p.segments
    );
    assert!(
        p.segments
            .iter()
            .all(|s| (s.a[0] == s.b[0] && s.a[0].abs() == 4.)
                || (s.a[1] == s.b[1] && (s.a[1] == 0. || s.a[1] == 100.))),
        "{:?}",
        p.segments
    );
}

fn stepped_frames() -> Vec<Triangle> {
    let mut tris = vec![];
    for i in 0..6 {
        let x = i as f64 * 300.;
        tris.extend(box_triangles(
            [x - 150., -25., 245.],
            [x + 150., 25., 296.845],
        ));
        for center in [x - 140., x + 140.] {
            tris.extend(box_triangles(
                [center - 14., -25., 0.],
                [center + 14., 25., 300.],
            ));
        }
    }
    tris
}
fn bounded_patch(tris: &[Triangle], bounds: Bounds) -> Patch {
    let input: Vec<_> = tris
        .iter()
        .copied()
        .filter(|t| {
            overlap(
                t,
                Bounds {
                    min: bounds.min.map(|v| v - 100.),
                    max: bounds.max.map(|v| v + 100.),
                },
            )
        })
        .collect();
    derive(
        &input,
        bounds,
        &mut |b| tris.iter().copied().filter(|t| overlap(t, b)).collect(),
        &Config::default(),
    )
    .unwrap()
}
#[test]
fn adjacent_supported_steps_form_continuous_outer_paths_without_whitelists() {
    let tris = stepped_frames();
    let p = bounded_patch(
        &tris,
        Bounds {
            min: [-2000.; 3],
            max: [3000.; 3],
        },
    );
    assert!(p.census.supported_step_lifts >= 6);
    assert_eq!(p.segments.len(), 4, "{:?} {:?}", p.census, p.segments);
    assert!(p.segments.iter().all(|s| s.a[2] == 300. && s.b[2] == 300.));
    assert_eq!(
        p.segments
            .iter()
            .filter(|s| (s.b[0] - s.a[0]) > 1800.)
            .count(),
        2
    );
}
#[test]
fn complete_edge_support_beyond_the_cell_halo_matches_both_sides() {
    let mut tris = box_triangles([-1500., -25., -30.], [1500., 25., 0.]);
    tris.extend(box_triangles([1400., -25., -30.], [1600., 25., 3.]));
    let cell = |lo, hi| Bounds {
        min: [lo, -500., -500.],
        max: [hi, 500., 500.],
    };
    let a = bounded_patch(&tris, cell(-3200., 0.));
    let b = bounded_patch(&tris, cell(0., 3200.));
    let cuts = |p: &Patch| {
        let mut v: Vec<_> = p
            .segments
            .iter()
            .flat_map(|s| [s.a, s.b])
            .filter(|p| p[0] == 0.)
            .map(|p| p.map(f64::to_bits))
            .collect();
        v.sort();
        v
    };
    assert_eq!(cuts(&a), cuts(&b));
    assert_eq!(cuts(&a).len(), 2, "{:?}", a.segments);
    assert!(
        a.segments
            .iter()
            .filter(|s| s.b[0] == 0.)
            .all(|s| s.a[2] == 3. && s.b[2] == 3.)
    );
    assert!(
        a.dependencies.iter().any(|b| b.max[0] > 1500.),
        "Full-span support must invalidate on distant post changes"
    );
}
#[test]
fn rotated_sloped_steps_preserve_native_cell_seams_and_real_obstacles() {
    let shift = [320_000., 640_000., 5000.];
    let tris: Vec<_> = rotated(&stepped_frames(), 0.645771823, -0.25)
        .into_iter()
        .map(|t| t.map(|p| std::array::from_fn(|i| p[i] + shift[i])))
        .collect();
    let cut = shift[0] + 500.;
    let cell = |lo, hi| Bounds {
        min: [lo, shift[1] - 3000., shift[2] - 3000.],
        max: [hi, shift[1] + 3000., shift[2] + 3000.],
    };
    let a = bounded_patch(&tris, cell(shift[0] - 3000., cut));
    let b = bounded_patch(&tris, cell(cut, shift[0] + 3000.));
    let cuts = |p: &Patch| {
        let mut v: Vec<_> = p
            .segments
            .iter()
            .flat_map(|s| [s.a, s.b])
            .filter(|p| p[0] == cut)
            .map(|p| {
                [
                    (p[0] / 100.) as f32,
                    (p[2] / 100.) as f32,
                    (-p[1] / 100.) as f32,
                ]
                .map(f32::to_bits)
            })
            .collect();
        v.sort();
        v.dedup();
        v
    };
    assert_eq!(cuts(&a), cuts(&b), "{:?} / {:?}", a.census, b.census);
    // The tilted post also has a genuine transverse top edge intersecting this
    // cut. Require matching native endpoints and both long outer continuations,
    // rather than incorrectly asserting that only two physical edges exist.
    assert!(cuts(&a).len() >= 2);
    assert_eq!(
        a.segments
            .iter()
            .filter(|s| s.b[0] == cut && s.b[0] - s.a[0] > 300.)
            .count(),
        2
    );
    let mut blocked = stepped_frames();
    blocked.extend(box_triangles([500., -35., 296.], [520., -15., 340.]));
    let p = bounded_patch(
        &blocked,
        Bounds {
            min: [-2000.; 3],
            max: [3000.; 3],
        },
    );
    assert!(
        !p.segments
            .iter()
            .any(|s| s.a[1] == -25. && s.b[1] == -25. && s.a[0] < 500. && s.b[0] > 520.),
        "Obstruction was bridged"
    );
}

/// Optional caller-owned geometry audit. No proprietary fixture is distributed.
#[test]
#[ignore]
fn audit_external_owned_geometry() {
    let input = std::env::var("S3_EDGE_AUDIT_INPUT").expect("Provide external fixture JSON");
    let output = std::env::var("S3_EDGE_AUDIT_OUTPUT").expect("Provide private audit output path");
    let input: serde_json::Value = serde_json::from_slice(&std::fs::read(input).unwrap()).unwrap();
    let mut rows = vec![];
    for fixture in input["fixtures"].as_array().unwrap() {
        let tris: Vec<Triangle> = serde_json::from_value(fixture["triangles_cm"].clone()).unwrap();
        for yaw in if fixture["world_space"].as_bool() == Some(true) {
            vec![0.]
        } else {
            vec![0., 0.645771823]
        } {
            let tris = rotated(&tris, yaw, 0.);
            let owner = Bounds {
                min: std::array::from_fn(|i| {
                    tris.iter()
                        .flatten()
                        .map(|v| v[i])
                        .fold(f64::INFINITY, f64::min)
                        - 100.
                }),
                max: std::array::from_fn(|i| {
                    tris.iter()
                        .flatten()
                        .map(|v| v[i])
                        .fold(f64::NEG_INFINITY, f64::max)
                        + 100.
                }),
            };
            let start = std::time::Instant::now();
            let p = derive(
                &tris,
                owner,
                &mut |b| tris.iter().copied().filter(|t| overlap(t, b)).collect(),
                &Config::default(),
            )
            .unwrap();
            // This is uncapped raw segment-AABB occupancy, NOT native selection.
            // Integration separately verifies actual source-provider candidates.
            let occupancy: Vec<_> = p
                .segments
                .iter()
                .map(|s| {
                    let center: [f64; 3] = std::array::from_fn(|i| (s.a[i] + s.b[i]) * 0.5);
                    p.segments
                        .iter()
                        .filter(|q| {
                            (0..3).all(|i| {
                                q.a[i].min(q.b[i]) <= center[i] + 100.
                                    && q.a[i].max(q.b[i]) >= center[i] - 100.
                            })
                        })
                        .count()
                })
                .collect();
            rows.push(serde_json::json!({"mesh":fixture["mesh"],"yaw_radians":yaw,"elapsed_ms":start.elapsed().as_secs_f64()*1000.,"census":p.census,"segments":p.segments,"raw_100cm_aabb_max":occupancy.into_iter().max().unwrap_or(0)}));
        }
    }
    std::fs::write(
        output,
        serde_json::to_vec_pretty(&serde_json::json!({"schema":1,"rows":rows})).unwrap(),
    )
    .unwrap();
}
