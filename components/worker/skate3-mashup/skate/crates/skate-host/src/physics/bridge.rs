use super::{GamePhysics, PlayerControls, SkaterRuntime};
use crate::{camera::CameraRuntime, graph_runtime::StockGraphs, input::ControllerInput};
use bevy::prelude::*;
use skate_data::skate_map::{Collision, Geometry, Rail, SkateMap};
use std::path::Path;
#[path = "bridge_summon.rs"]
mod summon;
pub use summon::SummonStatus;

#[derive(Clone, Copy, Default)]
pub struct Controls {
    pub buttons: u16,
    pub triggers: [u8; 2],
    pub left: [i16; 2],
    pub right: [i16; 2],
}
pub struct Session {
    physics: GamePhysics,
    skater: SkaterRuntime,
    controls: PlayerControls,
    graphs: StockGraphs,
    input: ControllerInput,
    camera: CameraRuntime,
    markers: crate::session_marker::Runtime,
    summon: summon::State,
}
pub struct Pose {
    pub root: Mat4,
    pub bones: Vec<Mat4>,
    pub names: Vec<String>,
    pub camera: Option<(Vec3, Mat3, f32)>,
    pub velocity: Vec3,
    pub tick: u64,
    pub state: String,
}
impl Session {
    pub fn new(
        root: &Path,
        triangles: Vec<[[f32; 3]; 3]>,
        rails: Vec<Vec<[f32; 3]>>,
        spawn: [f32; 3],
        heading: f32,
    ) -> Result<Self, String> {
        Self::new_geometry(root, triangles, rails, spawn, heading, None, false)
    }

    pub fn new_with_source(root: &Path,
        source: std::sync::Arc<dyn skate_core::physics::board_world::geometry_source::WorldGeometry>,
        rails: Vec<Vec<[f32; 3]>>, spawn: [f32; 3], heading: f32) -> Result<Self, String> {
        Self::new_geometry(root, Vec::new(), rails, spawn, heading, Some(source), false)
    }

    /// Explicit exposed admission for a host supplying ordinary triangle
    /// storage. The same Session and native contact producers are used.
    pub fn new_exposed(root: &Path, triangles: Vec<[[f32;3];3]>, rails: Vec<Vec<[f32;3]>>,
        spawn: [f32;3], heading: f32) -> Result<Self,String> {
        Self::new_geometry(root, triangles, rails, spawn, heading, None, true)
    }

    /// Dense exposed-edge imports use a bounded host admission policy. Stock
    /// providers and the original simulation's contact/acquisition math remain
    /// unchanged; only crowded authored candidate selection differs.
    pub fn new_with_source_exposed(root: &Path,
        source: std::sync::Arc<dyn skate_core::physics::board_world::geometry_source::WorldGeometry>,
        rails: Vec<Vec<[f32; 3]>>, spawn: [f32; 3], heading: f32) -> Result<Self, String> {
        Self::new_geometry(root, Vec::new(), rails, spawn, heading, Some(source), true)
    }

    fn new_geometry(root: &Path, triangles: Vec<[[f32; 3]; 3]>, rails: Vec<Vec<[f32; 3]>>,
        spawn: [f32; 3], heading: f32,
        source: Option<std::sync::Arc<dyn skate_core::physics::board_world::geometry_source::WorldGeometry>>,
        dense_exposed: bool) -> Result<Self, String> {
        let started = std::time::Instant::now();
        eprintln!("IW4L_SKATE_LOAD begin");
        skate_data::input_config::StockGameplayConfig::load(root).map_err(|e| e.to_string())?;
        let assets = skate_data::GameAssets::load(root).map_err(|e| e.to_string())?;
        let graphs = StockGraphs::load(root, &assets)?;
        let map = collision_map(triangles, rails, spawn, heading);
        eprintln!("IW4L_SKATE_LOAD graphs {}ms", started.elapsed().as_millis());
        let mut physics = match source {
            Some(source) => GamePhysics::load_map_source(root, &map,
                if dense_exposed { super::cached_geometry::memo(source) } else { source })?,
            None => GamePhysics::load_with_map(root, Some(&map))?,
        };
        eprintln!(
            "IW4L_SKATE_LOAD physics {}ms",
            started.elapsed().as_millis()
        );
        if dense_exposed {
            std::sync::Arc::get_mut(&mut physics.grind_world)
                .ok_or("Exposed provider was shared before host admission setup")?
                .enable_dense_authored()?;
        }
        let skater = SkaterRuntime::load(root, &graphs, &physics, "easy")?;
        eprintln!("IW4L_SKATE_LOAD skater {}ms", started.elapsed().as_millis());
        Ok(Self {
            physics,
            skater,
            controls: PlayerControls::load(root)?,
            graphs,
            input: ControllerInput::default(),
            camera: CameraRuntime::load(root)?,
            markers: crate::session_marker::Runtime::load(root)?,
            summon: summon::State::default(),
        })
    }
    /// A builder for collision to swap in later, usable on another thread.
    pub fn collision_builder(&self) -> CollisionBuilder {
        CollisionBuilder {
            material: self.physics.floor_material(),
            grind: std::sync::Arc::clone(&self.physics.grind_world),
        }
    }
    /// Swaps in collision built by `collision_builder`: the world the skater
    /// rides, climbs and grinds from the next tick.
    pub fn install_collision(&mut self, prepared: PreparedCollision) -> Result<(), String> {
        drop(self.swap_collision(prepared)?);
        Ok(())
    }
    /// Same atomic installation, returning retired immutable storage so a host
    /// can free large collision/grind tables away from its simulation thread.
    pub fn swap_collision(&mut self, prepared:PreparedCollision)->Result<PreparedCollision,String> {
        let (world,grind)=self.physics
            .install_world(prepared.world, std::sync::Arc::clone(&prepared.grind))?;
        self.skater.trajectory.bind_grind_world(prepared.grind);
        Ok(PreparedCollision{world,grind})
    }
    pub fn period(&self) -> f32 {
        self.physics.period().as_secs_f32()
    }
    pub fn set_low_camera(&mut self, low: bool) { self.camera.set_low_camera(low); }
    pub fn camera_mode(&self) -> &'static str { if self.camera.camera_type() == 0 { "low" } else { "high" } }
    /// Read-only host diagnostic through the same world and original thin-ray
    /// query used by the simulation. Coordinates are in simulation metres.
    pub fn surface_below(&self, point:[f32;3], above:f32, below:f32)
        -> Option<([f32;3],[f32;3])> {
        use skate_core::math::Vector3;
        let start=Vector3::new(point[0],point[1]+above,point[2]);
        let end=Vector3::new(point[0],point[1]-below,point[2]);
        self.physics.world().query_swept_line(start,end,0.).ok().flatten().map(|hit|{
            let p=hit.geometry.position;let n=hit.geometry.normal;
            ([p.x,p.y,p.z],[n.x,n.y,n.z])
        })
    }
    pub fn set_aspect_ratio(&mut self, aspect_ratio: f32) {
        if aspect_ratio.is_finite() && aspect_ratio > 0. {
            self.camera.set_aspect_ratio(aspect_ratio);
        }
    }
    /// Eagerly decode immutable animation banks before a map is ready.
    pub fn preload(root: &Path) -> Result<(), String> {
        crate::skater_animation::AnimationSource::load(root).map(|_| ())
    }
    /// Reuse the complete world and animation session. The original teleport path
    /// resets physical bodies and animation state at the new MW2 position.
    pub fn activate(&mut self, spawn: [f32; 3], heading: f32) -> Result<Pose, String> {
        self.position_for_entry(spawn, heading, true)
    }
    fn position_for_entry(&mut self, spawn: [f32; 3], heading: f32, on_board: bool) -> Result<Pose, String> {
        self.summon = summon::State::default();
        self.input = ControllerInput::default();
        self.markers.suspend();
        if self.physics.ticks == 0 {
            self.tick(Controls::default())?;
        }
        let mut transform = Mat4::from_rotation_translation(
            Quat::from_rotation_y(heading),
            Vec3::from_array(spawn),
        )
        .to_cols_array_2d();
        transform[3][3] = 0.;
        if on_board { self.skater.travel_to(transform)?; }
        else {
            self.skater.player_input.request_teleport(transform)?;
            self.skater.teleport_state.request_manual(transform, false);
        }
        for _ in 0..4 {
            self.tick(Controls::default())?;
        }
        self.input = ControllerInput::default();
        Ok(self.pose())
    }
    pub fn collect(&mut self, frame: InputFrame, dt: f32) {
        self.input.collect(frame.samples);
        self.markers.collect_time(f64::from(dt));
    }
    pub fn suspend_input(&mut self) {
        self.input = ControllerInput::default();
        self.markers.suspend();
    }
    pub fn advance(&mut self) -> Result<(), String> {
        self.input.publish_actions();
        self.advance_published()
    }
    fn advance_published(&mut self) -> Result<(), String> {
        let published = self.input.tick_input();
        self.markers
            .advance(&self.input, &self.physics, &mut self.skater);
        let (mut actions, automatic_input) = self.summon_actions(published.actions());
        self.controls.update_for_physics(
            &mut actions,
            &self.physics,
            &self.skater,
            &self.camera,
        )?;
        self.controls.publish_gestures(
            self.physics.animation_profile.physics_mode,
            self.skater.player_input.physical.state.state_16,
        );
        super::frame::advance(
            &mut self.physics,
            &mut self.skater,
            &mut self.controls,
            &self.graphs,
            &mut actions,
            published.controller_available() || automatic_input,
            &mut self.camera,
        )
    }
    /// Deterministic raw-packet entry point for playback/diagnostics.
    pub fn tick(&mut self, input: Controls) -> Result<(), String> {
        crate::input::sample(
            &mut self.input,
            skate_core::input::xbox::XboxState {
                buttons: input.buttons,
                triggers: input.triggers,
                left: input.left,
                right: input.right,
            },
        );
        self.advance_published()
    }
    pub fn pose(&self) -> Pose {
        let v = self.physics.board.bodies()[skate_core::physics::board::BodyId::Deck.index()]
            .rates
            .linear_velocity;
        Pose {
            root: crate::animation::native_matrix(
                self.skater.animated_skeleton.roots.animation_to_world,
            ),
            bones: self
                .skater
                .render_pose
                .iter()
                .map(|m| crate::animation::native_matrix(*m))
                .collect(),
            names: self.skater.animation.evaluator.frames.bone_names.clone(),
            camera: self.camera.frame.as_ref().map(|f| {
                (
                    Vec3::new(f.position[0], f.position[1], f.position[2]),
                    Mat3::from_cols_array_2d(&f.basis.columns),
                    f.field_of_view_degrees,
                )
            }),
            velocity: Vec3::new(v.x, v.y, v.z),
            tick: self.physics.ticks,
            state: format!("{:?}", self.skater.player_state.current()),
        }
    }
}

/// Collision for `Session::install_collision`, built off the simulation.
pub struct PreparedCollision {
    world: skate_core::physics::board_world::BoardWorld,
    grind: std::sync::Arc<crate::grind_world::StaticProvider>,
}

#[derive(Clone)]
pub struct CollisionBuilder {
    material: skate_core::physics::contact::RetailContactMaterial,
    grind: std::sync::Arc<crate::grind_world::StaticProvider>,
}

/// Owners in the initial exposed import are rail ordinals plus one. A scene
/// graph retires touched owners and assigns monotonically fresh owners to its
/// replacement rails, so retained contacts cannot bind unrelated geometry.
pub struct ExposedRailDelta {
    pub retired_owners: Vec<u64>,
    pub rails: Vec<(u64, Vec<[f32;3]>)>,
}

/// Build the unchanged imported-triangle adapter without creating a skater.
/// Used by host storage adapters to verify geometric/query equivalence.
pub fn imported_collision_world(triangles: Vec<[[f32;3];3]>, material: skate_core::physics::contact::RetailContactMaterial)
    -> Result<skate_core::physics::board_world::BoardWorld,String> {
    crate::skate_world::collision_world(&collision_map(triangles,Vec::new(),[0.;3],0.),material)
}

impl CollisionBuilder {
    /// Triangle-storage counterpart of the streamed-source delta adapter.
    pub fn build_exposed_delta(&self, triangles: Vec<[[f32;3];3]>, delta: ExposedRailDelta)
        -> Result<PreparedCollision,String> {
        let map = collision_map(triangles, Vec::new(), [0.;3], 0.);
        let world = crate::skate_world::collision_world(&map,self.material)?;
        let grind = if delta.retired_owners.is_empty() && delta.rails.is_empty() {
            std::sync::Arc::clone(&self.grind)
        } else {
            crate::grind_world::StaticProvider::exposed_delta(std::sync::Arc::clone(&self.grind),delta.retired_owners,delta.rails)?
        };
        Ok(PreparedCollision { world, grind })
    }
    /// Reuse the original global native grind tree and all unchanged storage.
    /// Only the changed connected networks are decoded into an immutable
    /// overlay; installation still uses the ordinary atomic world swap.
    pub fn build_source_exposed_delta(&self,
        source: std::sync::Arc<dyn skate_core::physics::board_world::geometry_source::WorldGeometry>,
        delta: ExposedRailDelta) -> Result<PreparedCollision, String> {
        let source = if self.grind.is_exposed() { super::cached_geometry::memo(source) } else { source };
        let mut world = skate_core::physics::board_world::BoardWorld::from_source(source, self.material).map_err(str::to_owned)?;
        world.enable_imported_floor_seams();
        let grind = if delta.retired_owners.is_empty() && delta.rails.is_empty() {
            std::sync::Arc::clone(&self.grind)
        } else {
            crate::grind_world::StaticProvider::exposed_delta(std::sync::Arc::clone(&self.grind), delta.retired_owners, delta.rails)?
        };
        Ok(PreparedCollision { world, grind })
    }
    pub fn build_source(&self,
        source: std::sync::Arc<dyn skate_core::physics::board_world::geometry_source::WorldGeometry>,
        rails: Vec<Vec<[f32; 3]>>) -> Result<PreparedCollision, String> {
        let map = collision_map(Vec::new(), rails, [0.; 3], 0.);
        let mut world = skate_core::physics::board_world::BoardWorld::from_source(source, self.material).map_err(str::to_owned)?;
        world.enable_imported_floor_seams();
        Ok(PreparedCollision {
            world,
            grind: std::sync::Arc::new(crate::grind_world::StaticProvider::new(Some(&map))?),
        })
    }

    /// Same physical world construction, with explicit dense authored admission.
    pub fn build_source_exposed(&self,
        source: std::sync::Arc<dyn skate_core::physics::board_world::geometry_source::WorldGeometry>,
        rails: Vec<Vec<[f32;3]>>) -> Result<PreparedCollision,String> {
        let mut prepared=self.build_source(super::cached_geometry::memo(source),rails)?;
        std::sync::Arc::get_mut(&mut prepared.grind)
            .ok_or("New exposed provider unexpectedly shared")?.enable_dense_authored()?;
        Ok(prepared)
    }

    pub fn build(
        &self,
        triangles: Vec<[[f32; 3]; 3]>,
        rails: Vec<Vec<[f32; 3]>>,
    ) -> Result<PreparedCollision, String> {
        let map = collision_map(triangles, rails, [0.; 3], 0.);
        Ok(PreparedCollision {
            world: crate::skate_world::collision_world(&map, self.material)?,
            grind: std::sync::Arc::new(crate::grind_world::StaticProvider::new(Some(&map))?),
        })
    }
}

/// IW4L's collision as a Skate map: one material, the triangles and rails.
fn collision_map(
    triangles: Vec<[[f32; 3]; 3]>,
    rails: Vec<Vec<[f32; 3]>>,
    spawn: [f32; 3],
    heading: f32,
) -> SkateMap {
    SkateMap {
            version: 14,
            name: "IW4L collision".into(),
            spawn,
            heading,
            environment: vec![],
            materials: vec![skate_data::skate_map::Material {
                name: "MW2".into(),
                flags: 0,
                friction: 0.8,
                restitution: 0.,
                color: [1.; 3],
                roughness: 1.,
                emissive: 0.,
                textures: [0; 5],
                indirect_strength: 1.,
                alpha_mode: 0,
                alpha_cutoff: 0.5,
                audio: 0,
                physics: 0,
                pattern: 0,
                depth_layer: None,
                retail_definition: None,
            }],
            textures: vec![],
            geometry: Geometry {
                vertices: vec![],
                indices: vec![],
                collision: triangles
                    .into_iter()
                    .map(|points| Collision {
                        points,
                        surface: 0,
                        material: 1,
                        native_edges: None,
                    })
                    .collect(),
            },
            rails: rails
                .into_iter()
                .enumerate()
                .map(|(i, p)| Rail {
                    name: format!("iw4_edge_{i}"),
                    closed: false,
                    points: p,
                    native: None,
                })
                .collect(),
            doors: vec![],
            lights: vec![],
            routes: vec![],
            extensions: vec![],
    }
}

/// The source engine's raw XInput transport. No Bevy deadzones, button remaps,
/// trigger reconstruction or rounding are inserted ahead of its native Pad.
#[derive(Default)]
pub struct ControllerTransport {
    capabilities: [crate::input::platform::CapabilityCache; 4],
}
pub struct InputFrame {
    samples: [Result<crate::input::platform::DevicePacket, crate::input::platform::DeviceError>; 4],
}
impl ControllerTransport {
    pub fn poll(&mut self) -> InputFrame {
        InputFrame {
            samples: std::array::from_fn(|i| {
                crate::input::platform::poll_cached(i, &mut self.capabilities[i])
            }),
        }
    }
}
impl InputFrame {
    pub fn neutral() -> Self {
        Self {
            samples: std::array::from_fn(|_|Err(crate::input::platform::DeviceError::Disconnected)),
        }
    }
    /// One controller in the first slot, already in XInput's layout:
    /// button bits, trigger bytes and signed stick axes.
    pub fn from_pad(buttons: u16, triggers: [u8; 2], left: [i16; 2], right: [i16; 2], packet: u32) -> Self {
        let mut frame = Self::neutral();
        frame.samples[0] = Ok(crate::input::platform::DevicePacket {
            number: packet,
            state: skate_core::input::xbox::XboxState { buttons, triggers, left, right },
            subtype: 1,
        });
        frame
    }
    pub fn controller(&self) -> Option<usize> {
        self.samples.iter().position(Result::is_ok)
    }
    pub fn buttons(&self) -> u16 {
        self.samples
            .iter()
            .find_map(|s| s.as_ref().ok().map(|s| s.state.buttons))
            .unwrap_or(0)
    }
}
