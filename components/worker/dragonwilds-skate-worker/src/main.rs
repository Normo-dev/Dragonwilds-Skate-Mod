//! Transport adapter for the existing mashup's Session. No skating solver here.
use serde::Deserialize;
use serde_json::{json, Value};
use skate_host::bridge::{Controls, ControllerTransport, InputFrame, Pose, Session};
use skate_host::bridge::PreparedCollision;
use std::{io::{self, BufRead, Write}, path::PathBuf};
#[path = "../../skate3-mashup/crates/render_anim/src/skate/rails.rs"]
mod example_rails;
mod rail_cache;
mod compact_world;
mod compact_geometry;
mod compact_rails;
mod scene_rail_cache;
mod world_runtime;
mod merged_rail_cache;
mod accepted_scene_cache;
mod building_rail_conditioner;
mod exposed_edges;
mod exposed_edge_cache;
mod exposed_rail_cache;
mod exposed_query;
mod exposed_resident;
mod grind_path_index;
mod legacy_cache_read;
mod maintenance;

// Dense host windows can exceed the old one-million-triangle ceiling. Sixteen
// million bounds the binary payload at 576 MB. Allocation still uses only the
// file's validated actual count; both transport paths share the same limit.
const MAX_TRIANGLES:usize=16_000_000;
// A triangle contributes at most three edges to imported grind derivation.
// Host runtime owners are wide; individual Pegasus conversion blobs remain u16.
const MAX_RAILS:usize=MAX_TRIANGLES*3;

#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct Pad {
    #[serde(default)] buttons: u16,
    #[serde(default)] triggers: [u8; 2],
    #[serde(default)] left: [i16; 2],
    #[serde(default)] right: [i16; 2],
}
impl From<Pad> for Controls {
    fn from(p: Pad) -> Self {
        Self { buttons: p.buttons, triggers: p.triggers, left: p.left, right: p.right }
    }
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Command {
    MaintenanceStatus,
    MaintenanceBegin { worker_id:String },
    MaintenanceEnd { worker_id:String,lease_token:String },
    PrepareWorld { manifest:PathBuf,#[serde(default)] scene_file:Option<PathBuf>,#[serde(default)] assets:Option<PathBuf>,#[serde(default="yes")] exposed_edges:bool },
    InitWorld { assets:PathBuf,manifest:PathBuf,anchor:[f64;3],#[serde(default)] scene_file:Option<PathBuf>,
        #[serde(default)] rail_file:Option<PathBuf>,#[serde(default)] scene_cache_required:bool,spawn:[f32;3],heading:f32 },
    WorldScene {revision:u32,scene_file:PathBuf},
    Surface {point:[f32;3],above:f32,below:f32},
    Camera { mode: CameraMode },
    Init { assets: PathBuf, #[serde(default)] triangles: Vec<[[f32; 3]; 3]>, #[serde(default)] collision_file: Option<PathBuf>, rails: Vec<Vec<[f32; 3]>>,
           spawn: [f32; 3], heading: f32, #[serde(default)] derive_rails: bool },
    Activate { spawn: [f32; 3], heading: f32 },
    Summon { spawn: [f32; 3], heading: f32 },
    Tick { #[serde(default)] controls: Pad, #[serde(default = "one")] steps: u32 },
    Poll { #[serde(default = "one")] steps: u32, #[serde(default)] mirror_steering: bool },
    Collision { #[serde(default)] triangles: Vec<[[f32; 3]; 3]>, #[serde(default)] collision_file: Option<PathBuf>, rails: Vec<Vec<[f32; 3]>>, #[serde(default)] derive_rails: bool },
    QueueCollision { revision:u32, #[serde(default)] triangles: Vec<[[f32; 3]; 3]>, #[serde(default)] collision_file: Option<PathBuf>, rails: Vec<Vec<[f32; 3]>>, #[serde(default)] derive_rails: bool },
    Pose,
    Shutdown,
}
impl Command {
    /// Exhaustive by design: a new command must explicitly declare whether it
    /// accesses cache inputs or may build/write geometry-derived storage.
    fn cache_access(&self) -> bool {
        match self {
            Self::PrepareWorld{..}|Self::InitWorld{..}|Self::WorldScene{..}|
            Self::Init{..}|Self::Collision{..}|Self::QueueCollision{..} => true,
            Self::MaintenanceStatus|Self::MaintenanceBegin{..}|Self::MaintenanceEnd{..}|
            Self::Surface{..}|Self::Camera{..}|Self::Activate{..}|Self::Summon{..}|
            Self::Tick{..}|Self::Poll{..}|Self::Pose|Self::Shutdown => false,
        }
    }
}
#[derive(Deserialize)]
#[serde(rename_all="lowercase")]
enum CameraMode { High, Low }
fn one() -> u32 { 1 }
fn yes() -> bool { true }

fn collision_input(triangles:Vec<[[f32;3];3]>,path:Option<PathBuf>)->Result<Vec<[[f32;3];3]>,String> {
    let Some(path)=path else {return Ok(triangles)};
    if !triangles.is_empty() {return Err("Use collision_file or triangles, not both".into())}
    use std::io::Read;
    let mut file=std::fs::File::open(path).map_err(|e|e.to_string())?;
    let mut header=[0u8;8];file.read_exact(&mut header).map_err(|e|e.to_string())?;
    let count=u32::from_le_bytes(header[4..8].try_into().unwrap()) as usize;
    if &header[..4]!=b"S3T1" || count==0 || count>MAX_TRIANGLES {return Err("Invalid collision file header".into())}
    let payload_size=count.checked_mul(36).ok_or("Collision file size overflow")?;
    let file_size=payload_size.checked_add(8).ok_or("Collision file size overflow")?;
    if file.metadata().map_err(|e|e.to_string())?.len()!=file_size as u64 {return Err("Collision file size mismatch".into())}
    let mut bytes=vec![0u8;payload_size];file.read_exact(&mut bytes).map_err(|e|e.to_string())?;
    let triangles=bytes.chunks_exact(36).map(|chunk|std::array::from_fn(|vertex|
        std::array::from_fn(|axis| {let offset=(vertex*3+axis)*4;f32::from_le_bytes(chunk[offset..offset+4].try_into().unwrap())}))).collect();
    Ok(triangles)
}

// All input I/O, validation, rail derivation and acceleration building for a
// queued update run on its builder thread. The live Session keeps its world.
fn world_input(triangles:Vec<[[f32;3];3]>,path:Option<PathBuf>,mut rails:Vec<Vec<[f32;3]>>,derive:bool)
    ->Result<(Vec<[[f32;3];3]>,Vec<Vec<[f32;3]>>),String> {
    let triangles=collision_input(triangles,path.clone())?;
    validate_world(&triangles,&rails)?;
    if derive { rails.extend(rail_cache::derive(path.as_deref(),&triangles)); }
    validate_world(&triangles,&rails)?;
    Ok((triangles,rails))
}

// Input transport only: preserve the example's raw XInput packet and original
// input processing while correcting the host's horizontal steering convention.
#[repr(C)]
#[derive(Default)]
struct RawGamepad { buttons:u16, lt:u8, rt:u8, lx:i16, ly:i16, rx:i16, ry:i16 }
#[repr(C)]
#[derive(Default)]
struct RawState { packet:u32, pad:RawGamepad }
#[link(name="xinput")]
unsafe extern "system" { fn XInputGetState(index:u32,state:*mut RawState)->u32; }
fn remap_steering(frame:InputFrame) -> InputFrame {
    let Some(index)=frame.controller() else { return frame; };
    let mut raw=RawState::default();
    // Exact Windows SDK ABI; storage is writable and read only after success.
    if unsafe { XInputGetState(index as u32,&mut raw) } != 0 { return InputFrame::neutral(); }
    let p=raw.pad;
    InputFrame::from_pad(p.buttons,[p.lt,p.rt],[p.lx.saturating_neg(),p.ly],[p.rx,p.ry],raw.packet)
}
fn find_rails(triangles: &[[[f32; 3]; 3]]) -> Vec<Vec<[f32; 3]>> {
    use bevy::math::Vec3;
    // Keep the example's inch-based rail thresholds exactly as authored.
    let map = triangles.iter().map(|t| t.map(|p| Vec3::new(p[0], -p[2], p[1]) / 0.0254)).collect::<Vec<_>>();
    let (rails, census) = example_rails::find(&map);
    eprintln!("SOURCE_RAILS candidates={} lips={} runs={} rails={}", census.candidates,census.lips,census.runs,census.rails);
    rails.into_iter().map(|r| r.into_iter().map(|p| [p.x*0.0254,p.z*0.0254,-p.y*0.0254]).collect()).collect()
}

fn valid_point(p: &[f32; 3]) -> bool { p.iter().all(|v| v.is_finite() && v.abs() < 1e7) }
fn validate_world(tris: &[[[f32; 3]; 3]], rails: &[Vec<[f32; 3]>]) -> Result<(), String> {
    if tris.is_empty() || tris.len() > MAX_TRIANGLES {
        return Err(format!("Collision needs 1..{MAX_TRIANGLES} host triangles; there is no default floor"));
    }
    if !tris.iter().flatten().all(valid_point) || rails.len() > MAX_RAILS
        || !rails.iter().all(|r| r.len() >= 2 && r.len() < 100_000 && r.iter().all(valid_point)) {
        return Err("Invalid collision coordinates or rails".into());
    }
    Ok(())
}
fn pose_value(p: Pose, period: f32) -> Result<Value, String> {
    if !p.root.is_finite() || !p.velocity.is_finite() || !p.bones.iter().all(|m| m.is_finite()) {
        return Err("Upstream simulation returned a non-finite pose".into());
    }
    if p.names.len() != p.bones.len() { return Err("Bone names and matrices disagree".into()); }
    Ok(json!({"ok":true, "period":period, "tick":p.tick, "state":p.state,
        "root":p.root.to_cols_array(), "velocity":p.velocity.to_array(),
        "bones":p.bones.iter().map(|b| b.to_cols_array()).collect::<Vec<_>>(),
        "names":p.names, "camera":p.camera.map(|(position,basis,fov)|
            json!({"position":position.to_array(),"basis":basis.to_cols_array(),"fov":fov}))}))
}
type PendingCollision=(u32,std::thread::JoinHandle<Result<PreparedCollision,String>>);
type PendingWorld=(u32,std::thread::JoinHandle<Result<(Option<PreparedCollision>,world_runtime::WorldRuntime),String>>);
fn retire(collision:Option<PreparedCollision>,world:Option<world_runtime::WorldRuntime>) {
    let _=std::thread::Builder::new().name("skate-retired-world".into()).spawn(move||drop((collision,world)));
}
fn execute(command: Command, session: &mut Option<Session>, pad: &mut ControllerTransport,
           pending:&mut Option<PendingCollision>,revision:&mut u32,
           collision_error:&mut Option<(u32,String)>,world:&mut Option<world_runtime::WorldRuntime>,
           pending_world:&mut Option<PendingWorld>,maintenance:&maintenance::Maintenance) -> Result<Value, String> {
    match &command {
        Command::MaintenanceStatus => return Ok(json!({"ok":true,"status":"maintenance_status"})),
        Command::MaintenanceBegin{worker_id} => {
            let token=maintenance.begin(worker_id,pending.is_some()||pending_world.is_some())?;
            return Ok(json!({"ok":true,"status":"maintenance_acquired","lease_token":token}));
        },
        Command::MaintenanceEnd{worker_id,lease_token} => {
            maintenance.end(worker_id,lease_token)?;
            return Ok(json!({"ok":true,"status":"maintenance_released"}));
        },
        _=>{},
    }
    // Acquire before any input read or derived-cache write. Synchronous jobs
    // keep this guard until return; asynchronous jobs move it into the closure.
    // Existing command-specific pending/reset policies are deliberately intact.
    let mut cache_job=if command.cache_access(){Some(maintenance.start_job()?)}else{None};
    if let Command::PrepareWorld{manifest,scene_file,assets,exposed_edges}=command {
        if session.is_some(){return Err("Offline preparation requires a fresh worker without an active Session".into());}
        return world_runtime::prepare_offline(&manifest,scene_file.as_deref(),assets.as_deref(),exposed_edges);
    }
    if let Command::InitWorld{assets,manifest,anchor,scene_file,rail_file,scene_cache_required,spawn,heading}=command {
        if anchor!=[0.;3]||!valid_point(&spawn)||!heading.is_finite(){return Err("Invalid fixed world origin/spawn".into());}
        let started=std::time::Instant::now();
        let (new_session,new_world)=world_runtime::WorldRuntime::init(&assets,&manifest,scene_file.as_deref(),rail_file.as_deref(),scene_cache_required,spawn,heading)?;
        *session=Some(new_session);*world=Some(new_world);*pending=None;*pending_world=None;*revision=0;*collision_error=None;
        world_runtime::cache_phase_ready("init",started);
        return Ok(json!({"ok":true,"status":"ready","period":session.as_ref().unwrap().period()}));
    }
    if let Command::Init { assets, triangles, collision_file, rails, spawn, heading, derive_rails } = command {
        if !valid_point(&spawn) || !heading.is_finite() { return Err("Invalid spawn".into()); }
        let (triangles,rails)=world_input(triangles,collision_file,rails,derive_rails)?;
        let new_session = Session::new(&assets, triangles, rails, spawn, heading)?;
        *pending=None;*pending_world=None;*world=None;*revision=0;*collision_error=None;
        *session = Some(new_session);
        return Ok(json!({"ok":true,"status":"ready","period":session.as_ref().unwrap().period()}));
    }
    let s = session.as_mut().ok_or("Initialize a session first")?;
    match command {
        Command::WorldScene{revision:requested,scene_file}=>{
            if requested<=*revision||pending.is_some()||pending_world.is_some(){return Err("Invalid or already pending world scene revision".into());}
            let runtime=world.as_ref().ok_or("Initialize the whole world first")?.clone();
            let builder=s.collision_builder();
            let job=cache_job.take().ok_or("Missing world-scene cache permit")?;
            let task=std::thread::Builder::new().name("skate-world-scene".into()).spawn(move||{
                let _cache_job=job;
                runtime.scene(builder,&scene_file)
            }).map_err(|e|e.to_string())?;
            *pending_world=Some((requested,task));*collision_error=None;
            Ok(json!({"ok":true,"status":"collision_queued"}))
        },
        Command::Surface{point,above,below}=>{
            if !valid_point(&point)||!above.is_finite()||!below.is_finite()||above<0.||below<0.||above>1000.||below>1000.{return Err("Invalid surface query".into());}
            Ok(json!({"ok":true,"surface":s.surface_below(point,above,below).map(|(position,normal)|json!({"position":position,"normal":normal}))}))
        },
        Command::Activate { spawn, heading } => {
            if !valid_point(&spawn) || !heading.is_finite() { return Err("Invalid spawn".into()); }
            let pose = s.activate(spawn, heading)?;
            pose_value(pose, s.period())
        },
        Command::Summon { spawn, heading } => {
            if !valid_point(&spawn) || !heading.is_finite() { return Err("Invalid spawn".into()); }
            let pose=s.summon(spawn,heading)?;
            pose_value(pose,s.period())
        },
        Command::Camera { mode } => {
            s.set_low_camera(matches!(mode, CameraMode::Low));
            Ok(json!({"ok":true,"camera_mode":s.camera_mode()}))
        },
        Command::Tick { controls, steps } => {
            if steps == 0 || steps > 600 { return Err("steps must be 1..600".into()); }
            let controls: Controls = controls.into();
            for _ in 0..steps { s.tick(controls)?; }
            pose_value(s.pose(), s.period())
        },
        Command::Poll { steps, mirror_steering } => {
            if steps == 0 || steps > 6 { return Err("poll steps must be 1..6".into()); }
            let mut connected = false;
            for _ in 0..steps {
                let frame = pad.poll();
                let frame = if mirror_steering { remap_steering(frame) } else { frame };
                connected = frame.controller().is_some();
                s.collect(frame, s.period());
                s.advance()?;
            }
            let mut response = pose_value(s.pose(), s.period())?;
            response["controller_connected"] = json!(connected);
            Ok(response)
        },
        Command::Collision { triangles, collision_file, rails, derive_rails } => {
            if world.is_some(){return Err("Use world_scene for a persistent whole-world session".into());}
            let (triangles,rails)=world_input(triangles,collision_file,rails,derive_rails)?;
            let prepared = s.collision_builder().build(triangles, rails)?;
            let retired=s.swap_collision(prepared)?;retire(Some(retired),None);
            *pending=None;*revision=0;*collision_error=None;
            Ok(json!({"ok":true,"status":"collision_installed"}))
        },
        Command::QueueCollision { revision:requested, triangles, collision_file, rails, derive_rails } => {
            if world.is_some(){return Err("Use world_scene for a persistent whole-world session".into());}
            if pending.is_some()||pending_world.is_some() { return Err("Collision build is already pending".into()); }
            let builder=s.collision_builder();
            let job=cache_job.take().ok_or("Missing collision-build cache permit")?;
            let task=std::thread::Builder::new().name("skate-collision-build".into()).spawn(move || {
                let _cache_job=job;
                let start=std::time::Instant::now();
                let (triangles,rails)=world_input(triangles,collision_file,rails,derive_rails)?;
                let result=builder.build(triangles,rails);
                eprintln!("COLLISION_BUILD revision={requested} elapsed_ms={:.3}",start.elapsed().as_secs_f64()*1000.);
                result
            }).map_err(|e|e.to_string())?;
            *pending=Some((requested,task));
            *collision_error=None;
            Ok(json!({"ok":true,"status":"collision_queued","collision_revision":*revision}))
        },
        Command::Pose => pose_value(s.pose(), s.period()),
        _ => unreachable!(),
    }
}
fn run() -> Result<(), String> {
    let maintenance=maintenance::Maintenance::new()?;
    let mut session:Option<Session> = None;
    let mut pad = ControllerTransport::default();
    let mut pending:Option<PendingCollision>=None;
    let mut world:Option<world_runtime::WorldRuntime>=None;
    let mut pending_world:Option<PendingWorld>=None;
    let mut revision=0u32;
    let mut collision_error:Option<(u32,String)>=None;
    let stdin = io::stdin();
    let mut stdout = io::BufWriter::new(io::stdout());
    for line in stdin.lock().lines() {
        let line = line.map_err(|e| e.to_string())?;
        let parsed = serde_json::from_str::<Command>(&line);
        if matches!(parsed, Ok(Command::Shutdown)) { break; }
        if pending.as_ref().is_some_and(|(_,task)|task.is_finished()) {
            let (requested,task)=pending.take().unwrap();
            let start=std::time::Instant::now();
            let result=task.join().map_err(|_|"Collision build thread panicked".to_owned()).and_then(|r|r)
                .and_then(|collision|session.as_mut().ok_or("Session unavailable".to_owned())?.swap_collision(collision))
                .map(|retired|retire(Some(retired),None))
                .map(|_|revision=requested);
            match result {
                Ok(())=>{collision_error=None;eprintln!("COLLISION_INSTALL revision={requested} elapsed_ms={:.3}",start.elapsed().as_secs_f64()*1000.);},
                Err(error)=>{eprintln!("COLLISION_ERROR revision={requested}: {error}");collision_error=Some((requested,error));},
            }
        }
        if pending_world.as_ref().is_some_and(|(_,task)|task.is_finished()) {
            let (requested,task)=pending_world.take().unwrap();let started=std::time::Instant::now();
            let result=task.join().map_err(|_|"World scene build thread panicked".to_owned()).and_then(|r|r)
                .and_then(|(collision,new_world)|{
                    let retired=match collision {
                        Some(collision)=>Some(session.as_mut().ok_or("Session unavailable".to_owned())?.swap_collision(collision)?),
                        None=>None,
                    };
                    let previous_world=world.replace(new_world);retire(retired,previous_world);revision=requested;Ok(())
                });
            match result {
                Ok(())=>{collision_error=None;eprintln!("WORLD_SCENE_INSTALL revision={requested} elapsed_ms={:.3}",started.elapsed().as_secs_f64()*1000.);world_runtime::cache_phase_ready("scene",started);},
                Err(error)=>{eprintln!("WORLD_SCENE_ERROR revision={requested}: {error}");collision_error=Some((requested,error));},
            }
        }
        let mut response = parsed.map_err(|e| e.to_string()).and_then(|c| execute(c, &mut session, &mut pad,&mut pending,&mut revision,&mut collision_error,&mut world,&mut pending_world,&maintenance))
            .unwrap_or_else(|e| json!({"ok":false,"error":e}));
        response["maintenance"]=maintenance_value(&maintenance,pending.is_some()||pending_world.is_some());
        response["collision_revision"]=json!(revision);
        if let Some(session)=&session {
            response["camera_mode"]=json!(session.camera_mode());
            let mount=session.summon_status();
            response["summon"]=json!({"phase":mount.phase,"error":mount.error,"motion_state":mount.motion_state,
                "holding_board":mount.holding_board,"supported":mount.supported,"locomotion":mount.locomotion});
        }
        response["collision_pending_revision"]=json!(pending.as_ref().map(|(requested,_)|requested).or_else(||pending_world.as_ref().map(|(requested,_)|requested)));
        if let Some(world)=&world {response["collision_scope"]=json!("whole_world");response["world"]=world.status();}
        if let Some((requested,error))=&collision_error {
            response["collision_error"]=json!(error);
            response["collision_error_revision"]=json!(requested);
        }
        serde_json::to_writer(&mut stdout, &response).map_err(|e| e.to_string())?;
        writeln!(stdout).and_then(|_| stdout.flush()).map_err(|e| e.to_string())?;
    }
    Ok(())
}
fn maintenance_value(maintenance:&maintenance::Maintenance,pending_result:bool)->Value {
    match maintenance.snapshot(pending_result) {
        Ok(state)=>json!({"schema":1,"worker_id":state.worker_id,"available":true,
            "active_cache_jobs":state.active_cache_jobs,"lease_active":state.lease_active,
            "pending_result":state.pending_result,"quiescent":state.quiescent,
            "retirement_allowed":state.retirement_allowed}),
        Err(error)=>json!({"schema":1,"worker_id":maintenance.worker_id(),"available":false,
            "quiescent":false,"retirement_allowed":false,"error":error}),
    }
}
fn main() {
    let result = std::thread::Builder::new().name("skate-session".into())
        .stack_size(32 * 1024 * 1024).spawn(run)
        .expect("Cannot start simulation worker").join().expect("Simulation worker panicked");
    if let Err(error) = result { eprintln!("{error}"); std::process::exit(1); }
}

#[cfg(test)]
mod maintenance_command_tests {
    use super::*;

    #[test]
    fn every_cache_entrypoint_is_blocked_before_io_or_session_validation() {
        let maintenance=maintenance::Maintenance::new().unwrap();
        let lease=maintenance.begin(maintenance.worker_id(),false).unwrap();
        let commands=[
            json!({"op":"prepare_world","manifest":"must-not-be-opened"}),
            json!({"op":"init_world","assets":"must-not-be-opened","manifest":"must-not-be-opened","anchor":[0,0,0],"spawn":[0,0,0],"heading":0}),
            json!({"op":"world_scene","revision":1,"scene_file":"must-not-be-opened"}),
            json!({"op":"init","assets":"must-not-be-opened","rails":[],"spawn":[0,0,0],"heading":0}),
            json!({"op":"collision","rails":[],"collision_file":"must-not-be-opened"}),
            json!({"op":"queue_collision","revision":1,"rails":[],"collision_file":"must-not-be-opened"}),
        ];
        let mut session=None;let mut pad=ControllerTransport::default();let mut pending=None;
        let mut revision=0;let mut error=None;let mut world=None;let mut pending_world=None;
        for value in commands {
            let command=serde_json::from_value::<Command>(value).unwrap();
            assert!(command.cache_access());
            let blocked=execute(command,&mut session,&mut pad,&mut pending,&mut revision,
                &mut error,&mut world,&mut pending_world,&maintenance).unwrap_err();
            assert!(blocked.contains("active maintenance lease"),"{blocked}");
            let state=maintenance.snapshot(false).unwrap();
            assert!(state.retirement_allowed);assert_eq!(state.active_cache_jobs,0);
        }
        maintenance.end(maintenance.worker_id(),&lease).unwrap();
    }

    #[test]
    fn resident_operations_are_explicitly_outside_the_cache_gate() {
        for value in [json!({"op":"pose"}),json!({"op":"tick"}),json!({"op":"poll"}),
            json!({"op":"camera","mode":"high"}),json!({"op":"shutdown"}),
            json!({"op":"surface","point":[0,0,0],"above":1,"below":2}),
            json!({"op":"activate","spawn":[0,0,0],"heading":0}),
            json!({"op":"summon","spawn":[0,0,0],"heading":0}),
            json!({"op":"maintenance_status"}),json!({"op":"maintenance_begin","worker_id":"worker"}),
            json!({"op":"maintenance_end","worker_id":"worker","lease_token":"lease"})] {
            assert!(!serde_json::from_value::<Command>(value).unwrap().cache_access());
        }
    }

    #[test]
    fn malformed_release_cannot_reach_the_coordinator_or_clear_lease() {
        let maintenance=maintenance::Maintenance::new().unwrap();
        let token=maintenance.begin(maintenance.worker_id(),false).unwrap();
        for value in [json!({"op":"maintenance_end"}),
            json!({"op":"maintenance_end","worker_id":maintenance.worker_id(),"lease_token":7}),
            json!({"op":"maintenance_end","worker_id":maintenance.worker_id(),"lease_token":token,"force":true})] {
            assert!(serde_json::from_value::<Command>(value).is_err());
            assert!(maintenance.snapshot(false).unwrap().lease_active);
        }
        let published=maintenance_value(&maintenance,false);
        assert_eq!(published["worker_id"],maintenance.worker_id());
        assert_eq!(published["retirement_allowed"],true);
        assert!(published.get("lease_token").is_none());
        maintenance.end(maintenance.worker_id(),&token).unwrap();
    }
}

#[cfg(test)]
mod collision_limit_tests {
    use super::*;

    #[test]
    fn required_scene_cache_is_opt_in_and_rejects_missing_scene_before_loading() {
        let value=json!({"op":"init_world","assets":"unused","manifest":"unused","anchor":[0,0,0],"spawn":[0,0,0],"heading":0});
        assert!(matches!(serde_json::from_value::<Command>(value.clone()).unwrap(),Command::InitWorld{scene_cache_required:false,..}));
        let mut required=value;required["scene_cache_required"]=true.into();
        assert!(matches!(serde_json::from_value::<Command>(required).unwrap(),Command::InitWorld{scene_cache_required:true,..}));
        let error=world_runtime::WorldRuntime::init(std::path::Path::new("unused"),std::path::Path::new("unused"),None,None,true,[0.;3],0.).err().unwrap();
        assert!(error.contains("Required scene cache needs a scene manifest"));
    }

    #[test]
    fn json_world_validation_accepts_limit_and_rejects_next_triangle() {
        let mut triangles=Vec::with_capacity(MAX_TRIANGLES+1);
        triangles.resize(MAX_TRIANGLES,[[0.,0.,0.],[0.,0.,1.],[1.,0.,0.]]);
        assert!(validate_world(&triangles,&[]).is_ok());
        triangles.push(triangles[0]);
        assert!(validate_world(&triangles,&[]).unwrap_err().contains(&MAX_TRIANGLES.to_string()));
    }

    #[test]
    fn binary_count_and_size_reject_before_payload_allocation() {
        let folder=PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap()
            .join("persistent-worker-limit-checks");
        std::fs::create_dir_all(&folder).unwrap();
        for (count,expected) in [(0,"header"),(MAX_TRIANGLES as u32,"size mismatch"),
                                  (MAX_TRIANGLES as u32+1,"header"),(u32::MAX,"header")] {
            let path=folder.join(format!("count-{count}.triangles"));
            let mut bytes=b"S3T1".to_vec();bytes.extend_from_slice(&count.to_le_bytes());
            std::fs::write(&path,bytes).unwrap();
            assert!(collision_input(Vec::new(),Some(path)).unwrap_err().contains(expected));
        }
    }
}
