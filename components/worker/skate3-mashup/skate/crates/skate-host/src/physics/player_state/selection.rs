//! CalcSuggestedState82D8ADE8 gets actual contacts, line distances and pose.
use super::*;
use skate_core::player::selector::{
    ProcessedStateInput, StateSelectionInput,
    conditions::{BoardBodyState, SkeletonAnimationState},
};
pub(super) fn advance(
    physics: &mut GamePhysics,
    skater: &mut SkaterRuntime,
    snapshot: skate_core::player::input_phase::ProcessedPhysicsSnapshot,
) -> Result<(), String> {
    debug_assert_eq!(snapshot.tick, physics.ticks);
    let p = &snapshot.input;
    let input = StateSelectionInput {
        processed: ProcessedStateInput {
            grind_type_1248: p.grind.family_1248,
            grind_candidate_1488: p.grind.valid_1488,
            grind_investigation_flags_1516: p.grind.flags_1516,
            field_1776: p.external_physics_1616.flags as i32,
            flags_2468: p.flags_2468,
            flags_2472: p.flags_2472,
            flags_2476: p.flags_2476,
            flags_2480: p.flags_2480,
            flags_2484: p.flags_2484,
            flags_2488: p.flags_2488,
            category_2512: p.category_2512,
            wheel_contact_count_2556: p.wheel_count_2556 as i32,
            field_2572: skater.player_state.post.state_frames,
            state_timer_2664: p.state_timer_2664,
            field_2732: skater.animation_input.fields.slide,
            field_2744: skater.animation_input.extra.revert_direction,
            trajectory_collision_time_2772: p.air_scalar_2772,
        },
        skateboard_contact_count_869: physics.riding.ground.wheel_contact_count,
        board_body: BoardBodyState {
            field_856: physics.riding.wheel_lines.minimum_distance,
            field_7692: physics.riding.ground.time_without_wheel_contact,
        },
        skeleton: SkeletonAnimationState {
            mode_16420: skater.skeleton_input.force_mode,
            back_chain_lane_12656: skater.skeleton_input.drive_frames[0][2][1],
            threshold_800: skater.player_state.animated_board_threshold,
        },
        normal_off_ground: skater.player_state.normal_off_ground,
        skitching_off_ground: skater.player_state.skitching_off_ground,
    };
    let current = skater.player_state.current();
    let requested = skater.player_state.selector.calculate(current, &input);
    // Observational adapter diagnostic: never changes source selection or queries.
    if grind_transition_trace_enabled()
        && current != requested && (current.is_grind() || requested.is_grind())
    {
        use std::sync::atomic::{AtomicU32, Ordering};
        static EVENTS: AtomicU32 = AtomicU32::new(0);
        if EVENTS.fetch_add(1, Ordering::Relaxed) < 512 {
            let g = &p.grind;
            let reasons: Vec<_> = skater.wipeout.state.reasons.iter().enumerate()
                .filter_map(|(i, &set)| set.then_some((i, skater.wipeout.state.values[i])))
                .collect();
            eprintln!("DRAGONWILDS_GRIND_TRANSITION {}", serde_json::json!({
                "tick": physics.ticks, "from": format!("{current:?}"), "to": format!("{requested:?}"),
                "wipeout_reasons": reasons, "wipeout_request_count": skater.wipeout.state.count,
                "candidate_valid": g.valid_1488, "family": g.family_1248,
                "geometry_kind": g.geometry_kind_1464, "geometry_flags": g.geometry_flags_1476,
                "investigation_flags": g.flags_1516, "owner": g.owner_1296,
                "primitive_start": g.primitive_start_1264.map(f32::from_bits),
                "primitive_end": g.primitive_end_1280.map(f32::from_bits),
                "point": g.point_1120.map(f32::from_bits),
                "direction": g.direction_1136.map(f32::from_bits),
                "normal": g.normal_1152.map(f32::from_bits),
                "surface_normal": g.upmost_normal_1408.map(f32::from_bits),
                "impact_speed": g.impact_speed_1492, "exit_lean": g.exit_lean_1500,
                "speed": p.scalar_2652, "velocity": p.vectors_400_416[0].map(f32::from_bits),
                "balance": skater.animation_input.fields.balance,
                "wheel_count": p.wheel_count_2556,
                "input_flags": [p.flags_2468,p.flags_2472,p.flags_2476,p.flags_2480,p.flags_2484,p.flags_2488]
            }));
        }
    }
    skater.player_state.requested_state = requested;
    if requested != current {
        //82DB5FF4 restores the actual counter also used by ground-history.
        skater.player_input.player.ground_history_frames_1304 = 100;
        super::transition::set(physics, skater, requested)?;
    }
    //SystemLogic82DB6000 clears the per-frame Wipeout request accumulator.
    skater.wipeout.state.clear_after_selection();
    Ok(())
}

/// Read once per process; ordinary play does not format or write diagnostics.
fn grind_transition_trace_enabled() -> bool {
    static ENABLED: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ENABLED.get_or_init(|| std::env::var_os("DWS_GRIND_DIAGNOSTICS").as_deref() == Some(std::ffi::OsStr::new("1")))
}
