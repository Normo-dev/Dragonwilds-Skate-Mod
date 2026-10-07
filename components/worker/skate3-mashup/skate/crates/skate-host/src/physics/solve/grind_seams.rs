//! Host diagnostics and narrowly scoped imported-geometry seam contact policy.
//! The source query, retained grind manager and native solver remain upstream.
use super::super::{GamePhysics, SkaterRuntime};
use skate_core::physics::board_world::BoardWorldVolume;
use skate_core::physics::{board::BodyId, board_step::BoardCollision};
use skate_core::physics::{board_step::CollisionBody, board_world::query_metadata::Bounds};
#[path = "grind_seams/footprint.rs"]
mod footprint;
#[path = "grind_seams/proof.rs"]
mod proof;
#[path = "grind_seams/witness.rs"]
mod witness;
use proof::{Frame, core, cross, dot, mul, sub, unit, v};

pub(super) fn adapt(
    contacts: &mut Vec<BoardCollision>,
    board_volumes: &[BoardWorldVolume],
    physics: &GamePhysics,
    skater: &SkaterRuntime,
) {
    if !physics.grind_world.is_exposed() || !skater.player_state.current().is_grind() {
        return;
    }
    let g = &skater.player_input.grind.investigation;
    if !g.valid_1488 {
        return;
    }
    let trace = tracing(physics.ticks);
    if trace {
        let deck = physics.board.part_transforms()[BodyId::Deck.index()];
        eprintln!(
            "GRIND_SEAM_CONTACTS tick={} family={} rail_point={:?} rail_start={:?} rail_end={:?} deck={deck:?} contacts={contacts:?}",
            physics.ticks,
            g.family_1248,
            g.point_1120.map(f32::from_bits),
            g.primitive_start_1264.map(f32::from_bits),
            g.primitive_end_1280.map(f32::from_bits)
        );
    }
    let raw = |a: [u32; 4]| {
        [
            f32::from_bits(a[0]) as f64,
            f32::from_bits(a[1]) as f64,
            f32::from_bits(a[2]) as f64,
        ]
    };
    let (truck, width) = skater.player_input.grind.contact_dimensions();
    let frame = Frame {
        start: raw(g.primitive_start_1264),
        end: raw(g.primitive_end_1280),
        direction: raw(g.direction_1136),
        deck: v(physics.board.part_transforms()[BodyId::Deck.index()].translation),
        truck: truck as f64,
        width: width as f64,
    };
    // Native supported-lip families: 50-50, boardslide, tipslide, 5-0.
    // Backslash/darkslide keep the original response.
    if !matches!(g.family_1248, 0 | 1 | 2 | 3) {
        if trace {
            for row in contacts.iter() {
                if row.body_b != CollisionBody::StaticWorld {
                    continue;
                }
                let at = row.contact.position_on_b;
                if let Some(triangles) = physics
                    .world
                    .bounded_triangles(Bounds { min: at, max: at }.expanded(0.20), 4096)
                {
                    let local = physical_frame(frame, row, board_volumes);
                    if let Some(p) = local.and_then(|local| {
                        proof::prove(local, v(at), v(row.contact.normal), &triangles)
                    }) {
                        eprintln!(
                            "GRIND_SEAM_UNSUPPORTED_FAMILY_PROOF tick={} family={} body={:?} contact={:?} normal={:?} support=[{},{}]",
                            physics.ticks,
                            g.family_1248,
                            row.body_a,
                            at,
                            row.contact.normal,
                            p.low,
                            p.high
                        );
                    }
                }
            }
        }
        return;
    }
    let Some(rail) = unit(sub(frame.end, frame.start)) else {
        return;
    };
    let Some(up) = unit(sub([0., 1., 0.], mul(rail, rail[1]))) else {
        return;
    };
    let side = cross(up, rail);
    // A bounded rare fallback for displaced upward board prism witnesses.
    // Exhaustion preserves subsequent original contacts.
    let mut recovery_leaves = witness::LeafCache::new();
    contacts.retain_mut(|row| {
        if !matches!(row.body_a, CollisionBody::Board(_))
            || row.body_b != CollisionBody::StaticWorld
        {
            return true;
        }
        let contact = v(row.contact.position_on_b);
        let normal = v(row.contact.normal);
        // Limit the proof to a small source geometry patch; an overflowing
        // query rejects adaptation rather than accepting incomplete evidence.
        if dot(normal, frame.direction) > -0.20 {
            return true;
        }
        let at = row.contact.position_on_b;
        let Some(triangles) = physics.world.bounded_triangles(
            Bounds { min: at, max: at }.expanded(0.20), 4096,
        ) else {
            return true;
        };
        let mut local = frame;
        if matches!(g.family_1248, 1 | 2) {
            // Board and tip slides can be transverse to the rail. Their collider
            // support point, not truck wheel-width, bounds the contact lane.
            let point = v(row.contact.position_on_a);
            let physical_extent = board_volumes.iter()
                .filter(|volume| volume.body == row.body_a)
                .find_map(|volume| {
                    let (center, radius) = super::super::network::bounds(volume.primitive);
                    footprint::extent(point, center.to_array().map(|v|v as f64), radius as f64, frame.deck, side)
                });
            let Some(physical_extent) = physical_extent else { return true; };
            local.width = local.width.max(physical_extent);
        }
        let mut recovered = None;
        let p = proof::prove(local, contact, normal, &triangles).or_else(|| {
            if g.family_1248 != 1 || dot(normal, up) <= 0.
                || dot(normal, up) > 0.80
                || dot(sub(contact, frame.start), up) <= 0.002 {
                return None;
            }
            for source in &triangles {
                if dot(v(source.triangle.feature.normal), up) < 0.60 {
                    continue;
                }
                if witness::projected_on_face(contact, source).is_none() {
                    continue;
                }
                for (ordinal,volume) in board_volumes.iter().enumerate().filter(|(_,volume)|volume.body==row.body_a) {
                    let(center,radius)=super::super::network::bounds(volume.primitive);
                    if footprint::extent(v(row.contact.position_on_a),center.to_array().map(|v|v as f64),radius as f64,frame.deck,side).is_none() {
                        continue;
                    }
                    let Some(projected) = recovery_leaves.recovered_point(row, volume, ordinal, physics.query, source) else { continue; };
                    let Some(p) = proof::prove(local, projected, normal, &triangles) else { continue; };
                    if trace {
                        eprintln!("GRIND_SEAM_WITNESS_RECOVERED tick={} body={:?} original={contact:?} projected={projected:?} source={source:?} volume={volume:?}",physics.ticks,row.body_a);
                    }
                    recovered = Some(projected);
                    return Some(p);
                }
            }
            None
        }).or_else(|| {
            if g.family_1248 != 2 { return None; }
            let p=proof::prove_coplanar_corner(local,contact,normal,&triangles)?;
            for source in &triangles {
                if !proof::coplanar_source(source,frame.start,up,p.direction) { continue; }
                let Some(projected)=witness::projected_on_face(contact,source) else { continue; };
                // This separate path changes only the normal. Its world witness
                // must already lie on the actual coplanar source face.
                if core(projected)!=row.contact.position_on_b { continue; }
                for (ordinal,volume) in board_volumes.iter().enumerate().filter(|(_,volume)|volume.body==row.body_a) {
                    let(center,radius)=super::super::network::bounds(volume.primitive);
                    if footprint::extent(v(row.contact.position_on_a),center.to_array().map(|v|v as f64),radius as f64,frame.deck,side).is_none() { continue; }
                    let matched=recovery_leaves.recovered_point(row,volume,ordinal,physics.query,source).is_some();
                    if trace && std::env::var_os("SKATE_GRIND_LEAF_DIAGNOSTICS").is_some() {
                        eprintln!("GRIND_SEAM_LEAF tick={} normal={normal:?} source={source:?} child={ordinal} replay_count={} matched={matched}",physics.ticks,recovery_leaves.replay_count());
                    }
                    if matched {
                        if trace { eprintln!("GRIND_SEAM_COPLANAR_CORNER tick={} body={:?} contact={contact:?} normal={normal:?} support=[{},{}]",physics.ticks,row.body_a,p.low,p.high); }
                        return Some(p);
                    }
                }
            }
            None
        });
        let Some(p) = p else {
            return true;
        };
        let remaining = sub(normal, mul(p.direction, dot(normal, p.direction)));
        if trace {
            eprintln!(
                "GRIND_SEAM_ADAPTED tick={} body={:?} contact={contact:?} normal={normal:?} support=[{},{}] remaining={remaining:?}",
                physics.ticks, row.body_a, p.low, p.high,
            );
        }
        // Keep the side and upward components, including exterior wall support.
        // Only the proved small lip's rail-opposing component is removed.
        if dot(remaining, remaining) < 0.0025 {
            return false;
        }
        if dot(remaining, p.up) < -0.05 {
            return true;
        }
        if let Some(projected) = recovered {
            row.contact.position_on_b = core(projected);
        }
        row.contact.normal = core(unit(remaining).expect("nonzero projected contact"));
        true
    });
    if trace {
        eprintln!("GRIND_SEAM_REPLAY_COUNT tick={} count={}",physics.ticks,recovery_leaves.replay_count());
    }
}

fn tracing(tick: u64) -> bool {
    static TRACE: std::sync::OnceLock<Option<(u64, u64)>> = std::sync::OnceLock::new();
    let window = TRACE.get_or_init(|| {
        std::env::var_os("SKATE_GRIND_SEAM_DIAGNOSTICS")?;
        let (start, end) = std::env::var("SKATE_GRIND_SEAM_DIAGNOSTIC_TICKS")
            .ok()
            .and_then(|s| {
                s.split_once(':')
                    .and_then(|(a, b)| Some((a.parse().ok()?, b.parse().ok()?)))
            })
            .unwrap_or((0, u64::MAX));
        Some((start, end))
    });
    window.is_some_and(|(start, end)| start <= tick && tick <= end)
}

fn physical_frame(
    frame: Frame,
    row: &BoardCollision,
    volumes: &[BoardWorldVolume],
) -> Option<Frame> {
    if !matches!(row.body_a, CollisionBody::Board(_)) {
        return None;
    }
    let rail = unit(sub(frame.end, frame.start))?;
    let up = unit(sub([0., 1., 0.], mul(rail, rail[1])))?;
    let side = cross(up, rail);
    let point = v(row.contact.position_on_a);
    let extent = volumes
        .iter()
        .filter(|volume| volume.body == row.body_a)
        .find_map(|volume| {
            let (center, radius) = super::super::network::bounds(volume.primitive);
            footprint::extent(
                point,
                center.to_array().map(|v| v as f64),
                radius as f64,
                frame.deck,
                side,
            )
        })?;
    Some(Frame {
        width: frame.width.max(extent),
        ..frame
    })
}

pub(super) fn observe(physics: &GamePhysics, skater: &SkaterRuntime) {
    if !physics.grind_world.is_exposed() || !tracing(physics.ticks) {
        return;
    }
    let deck = physics.board.part_transforms()[BodyId::Deck.index()];
    let velocity = physics.board.bodies()[BodyId::Deck.index()]
        .rates
        .linear_velocity;
    eprintln!(
        "GRIND_SEAM_MANAGER tick={} state={:?} deck={deck:?} velocity={velocity:?} manager={:?}",
        physics.ticks,
        skater.player_state.current(),
        skater.player_input.grind
    );
    for (index, row) in physics.board.solved_contacts().iter().enumerate() {
        let w = row.words();
        let impulse = row.accumulated_impulse();
        if w[31] >= skate_core::physics::board::BODY_COUNT as u32
            || w[43] != u32::MAX
            || impulse[0] <= 0.
        {
            continue;
        }
        let normal = [w[28], w[29], w[30]].map(f32::from_bits);
        let at = [w[4], w[5], w[6]].map(f32::from_bits);
        eprintln!(
            "GRIND_SEAM_SOLVED tick={} state={:?} index={index} part={:?} atB={at:?} normal={normal:?} impulse={impulse:?}",
            physics.ticks,
            skater.player_state.current(),
            BodyId::ORDER[w[31] as usize]
        );
    }
}
