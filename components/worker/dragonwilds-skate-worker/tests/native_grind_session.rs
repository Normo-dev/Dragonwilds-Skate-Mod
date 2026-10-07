//! Opt-in actual imported Session replays using the caller's converted assets.
//! Geometry is synthetic or explicitly supplied by the caller; inputs are test fixtures.
use skate_host::bridge::{Controls, ExposedRailDelta, Session};
fn assets() -> std::path::PathBuf {
    std::env::var_os("SKATE_NATIVE_SESSION_ASSETS")
        .expect("caller-owned SKATE_NATIVE_SESSION_ASSETS required")
        .into()
}
fn sequence() -> [(usize, Controls); 5] {
    [
        (75, Controls::default()),
        (
            120,
            Controls {
                buttons: 4096,
                ..Default::default()
            },
        ),
        (
            20,
            Controls {
                right: [0, -32767],
                ..Default::default()
            },
        ),
        (
            1,
            Controls {
                right: [0, 32767],
                ..Default::default()
            },
        ),
        (150, Controls::default()),
    ]
}
fn floor(y: f32, x0: f32, x1: f32, z0: f32, z1: f32) -> Vec<[[f32; 3]; 3]> {
    vec![
        [[x0, y, z0], [x0, y, z1], [x1, y, z1]],
        [[x0, y, z0], [x1, y, z1], [x1, y, z0]],
    ]
}
fn cube(lo: [f32; 3], hi: [f32; 3]) -> Vec<[[f32; 3]; 3]> {
    let p: Vec<[f32; 3]> = (0..8)
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
#[test]
#[ignore = "requires caller-owned converted source assets"]
fn offgrind_complete_native_pose_matches_legacy_host_exactly() {
    std::thread::Builder::new()
        .stack_size(16 * 1024 * 1024)
        .spawn(|| {
            let root = assets();
            let triangles = floor(0., -30., 30., -8., 8.);
            let spawn = [-14., 0.7, -0.05];
            let heading = std::f32::consts::FRAC_PI_2;
            let mut a = Session::new(&root, triangles.clone(), vec![], spawn, heading).unwrap();
            let mut b = Session::new_exposed(&root, triangles, vec![], spawn, heading).unwrap();
            a.activate(spawn, heading).unwrap();
            b.activate(spawn, heading).unwrap();
            for (n, input) in sequence() {
                for _ in 0..n {
                    a.tick(input).unwrap();
                    b.tick(input).unwrap();
                    let a = a.pose();
                    let b = b.pose();
                    assert_eq!(a.root, b.root, "tick {}", a.tick);
                    assert_eq!(a.bones, b.bones, "tick {}", a.tick);
                    assert_eq!(a.names, b.names);
                    assert_eq!(a.camera, b.camera);
                    assert_eq!(a.velocity, b.velocity);
                    assert_eq!(a.state, b.state);
                    assert_eq!(a.tick, b.tick);
                    assert!(!a.state.contains("Grind"));
                }
            }
        })
        .unwrap()
        .join()
        .unwrap();
}
#[test]
#[ignore = "requires caller-owned converted source assets; uses a 16 MiB test-thread stack"]
fn joined_piece_grind_survives_unrelated_retirement_and_releases_retired_active_owner() {
    std::thread::Builder::new()
        .stack_size(16 * 1024 * 1024)
        .spawn(|| {
            let root = assets();
            let mut triangles = cube([-1.5, 0., -0.25], [16.5, 3., 0.25]);
            triangles.extend(floor(2.5, -30., -1.54, -8., 8.));
            triangles.extend(floor(2.5, -30., 30., -0.8, -0.26));
            let rails: Vec<_> = (0..6)
                .flat_map(|i| {
                    [0.25, -0.25].into_iter().map(move |z| {
                        vec![[i as f32 * 3. - 1.5, 3., z], [i as f32 * 3. + 1.5, 3., z]]
                    })
                })
                .collect();
            let spawn = [-14., 3.5, -0.05];
            let heading = std::f32::consts::FRAC_PI_2;
            let mut session =
                Session::new_exposed(&root, triangles.clone(), rails.clone(), spawn, heading)
                    .unwrap();
            session.activate(spawn, heading).unwrap();
            let (mut unrelated, mut active, mut released, mut farthest) =
                (None, None, None, f32::NEG_INFINITY);
            for (n, input) in sequence() {
                for _ in 0..n {
                    session.tick(input).unwrap();
                    let p = session.pose();
                    let x = p.root.to_cols_array()[12];
                    let grinding = p.state.contains("Grind");
                    if grinding {
                        farthest = farthest.max(x);
                    }
                    if grinding && x >= 5. && unrelated.is_none() {
                        let next = session
                            .collision_builder()
                            .build_exposed_delta(
                                triangles.clone(),
                                ExposedRailDelta {
                                    retired_owners: vec![11, 12],
                                    rails: vec![(13, rails[10].clone()), (14, rails[11].clone())],
                                },
                            )
                            .unwrap();
                        session.install_collision(next).unwrap();
                        unrelated = Some(p.tick);
                    }
                    if grinding && x >= 8. && active.is_none() {
                        let next = session
                            .collision_builder()
                            .build_exposed_delta(
                                triangles.clone(),
                                ExposedRailDelta {
                                    retired_owners: vec![8],
                                    rails: vec![(15, vec![[20., 3., -0.25], [23., 3., -0.25]])],
                                },
                            )
                            .unwrap();
                        session.install_collision(next).unwrap();
                        active = Some(p.tick);
                    }
                    if !grinding && active.is_some_and(|t| p.tick > t) && released.is_none() {
                        released = Some(p.tick);
                    }
                }
            }
            assert!(
                unrelated.is_some() && farthest >= 8.,
                "native grind did not cross joined pieces and unrelated retirement"
            );
            assert!(
                active.is_some() && released.is_some_and(|t| t <= active.unwrap() + 10),
                "native grind retained a retired active rail"
            );
        })
        .unwrap()
        .join()
        .unwrap();
}

#[test]
#[ignore = "requires caller-owned converted assets and SKATE_NATIVE_SEAM_FIXTURE exported by seam_grind_contacts; uses a 16 MiB stack"]
fn native_board_grinds_cross_caller_owned_supported_seams() {
    std::thread::Builder::new()
        .stack_size(16 * 1024 * 1024)
        .spawn(|| {
            let path = std::env::var_os("SKATE_NATIVE_SEAM_FIXTURE")
                .expect("caller-owned exported seam fixture required");
            let data: serde_json::Value =
                serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
            let origin: [f64; 3] = serde_json::from_value(data["origin_cm"].clone()).unwrap();
            let anchor = [
                (origin[0] * 0.01) as f32,
                (origin[2] * 0.01) as f32,
                (-origin[1] * 0.01) as f32,
            ];
            let source: Vec<[[f64; 3]; 3]> =
                serde_json::from_value(data["triangles_cm"].clone()).unwrap();
            let mut triangles: Vec<_> = source
                .into_iter()
                .map(|t| {
                    t.map(|p| {
                        [
                            (p[0] * 0.01) as f32,
                            (p[2] * 0.01) as f32,
                            (-p[1] * 0.01) as f32,
                        ]
                    })
                })
                .collect();
            for t in floor(2.5, -30., -1.54, -8., 8.)
                .into_iter()
                .chain(floor(2.5, -30., 30., -0.8, -0.26))
            {
                triangles.push(t.map(|p| std::array::from_fn(|i| p[i] + anchor[i])));
            }
            let rails: Vec<Vec<[f32; 3]>> =
                serde_json::from_value(data["rails_m"].clone()).unwrap();
            let spawn = [-14. + anchor[0], 3.5 + anchor[1], -0.05 + anchor[2]];
            let heading = std::f32::consts::FRAC_PI_2;
            for spin in [-32767, 32767] {
            let mut session =
                Session::new_exposed(&assets(), triangles.clone(), rails.clone(), spawn, heading).unwrap();
            session.activate(spawn, heading).unwrap();
            let mut farthest = f32::NEG_INFINITY;
            let mut boardslide_ticks = 0;
            for (n, input) in sequence() {
                for _ in 0..n {
                    let mut controls = input;
                    // Native activation adds five ticks; this spans late crouch/pop
                    // and initial air without changing any source physical state.
                    if (216..236).contains(&session.pose().tick) {
                        controls.left = [spin, 0];
                    }
                    session.tick(controls).unwrap();
                    let p = session.pose();
                    if p.state == "GrindBoardslide" {
                        boardslide_ticks += 1;
                    }
                    if p.state.starts_with("Grind") {
                        farthest = farthest.max(p.root.to_cols_array()[12] - anchor[0]);
                    }
                }
            }
            assert!(
                boardslide_ticks > 30 && farthest >= 8.,
                "native board grind spin {spin} stopped at {farthest} after {boardslide_ticks} boardslide ticks"
            );
            }
        })
        .unwrap()
        .join()
        .unwrap();
}
