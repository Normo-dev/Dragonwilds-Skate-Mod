use crate::{compact_world::CompactWorld,compact_geometry::CompactGeometry,compact_rails::{self,LipBake}};
use skate_host::bridge::{Session,CollisionBuilder,PreparedCollision,ExposedRailDelta};
use std::{path::{Path,PathBuf},sync::Arc,time::Instant};
use serde_json::{Value,json};
use sha2::{Digest,Sha256};
#[path="cache_progress.rs"]
mod cache_progress;
pub use cache_progress::ready as cache_phase_ready;
#[path="world_runtime_graph.rs"]
mod graph;

#[derive(Clone)]
pub struct WorldRuntime {
    base:Arc<CompactWorld>,bake:Option<Arc<LipBake>>,pub source:Arc<CompactGeometry>,
    manifest:PathBuf,parameters:crate::exposed_edge_cache::Parameters,exposed:Option<Arc<crate::exposed_edge_cache::Snapshot>>,
    graph:Option<graph::GraphState>,resident:Option<Arc<crate::exposed_resident::Resident>>,
    pub rail_count:usize,status:Value,
}
fn source(base:&Arc<CompactWorld>,scene:Option<&Path>)->Result<Arc<CompactGeometry>,String> {
    let world=match scene {Some(path)=>Arc::new(CompactWorld::with_overlay(base,path)?),None=>base.clone()};
    Ok(Arc::new(CompactGeometry::new(world,[0.;3])?))
}
/// Derived scene rails are conditioned only after the immutable original cache
/// has been loaded/prepared. The physical source and original cache stay intact.
fn condition_rails(source:&CompactGeometry,scene:Option<&Path>,rails:Vec<Vec<[f32;3]>>)
    ->Result<(Vec<Vec<[f32;3]>>,Value),String> {
    let Some(scene)=scene else{return Ok((rails,Value::Null));};
    let fingerprint=source.world.scene_fingerprint.as_deref().ok_or("Conditioning scene identity missing")?;
    let started=Instant::now();
    let (rails,report)=crate::building_rail_conditioner::condition(scene,fingerprint,rails,|lo,hi| {
        source.world.candidate_triangles(lo,hi).into_iter().map(|id|source.world.raw_triangle(id)).collect()
    })?;
    let status=json!({"algorithm_sha256":Sha256::digest(include_bytes!("building_rail_conditioner.rs")).iter().map(|b|format!("{b:02x}")).collect::<String>(),
        "physical_geometry_changed":false,"raw_cache_changed":false,"report":report});
    eprintln!("WORLD_BUILDING_RAIL_CONDITIONING elapsed_ms={} {}",started.elapsed().as_millis(),status);
    Ok((rails,status))
}
/// Warm startup never repeats the global endpoint graph. Only explicit offline
/// preparation or a newly derived scene may construct its exact ordered cache.
fn exposed_rails(snapshot:&crate::exposed_edge_cache::Snapshot,allow_build:bool,operation:&str)
    ->Result<(Vec<Vec<[f32;3]>>,Value,Value),String> {
    match cache_progress::run(operation,"loading_cached_grind_data",||crate::exposed_rail_cache::load_with_record(snapshot)) {
        Ok(Some((rails,stitch,record))) => return Ok((rails,stitch,serde_json::to_value(record).map_err(|e|e.to_string())?)),
        Ok(None)=>{},
        Err(error)=>eprintln!("WORLD_EXPOSED_NATIVE_RAIL_CACHE rejected: {error}"),
    }
    if !allow_build { return Err("Prepared native grind index is missing or invalid; run offline setup repair before entering the world".into()); }
    let(rails,stitch)=cache_progress::run(operation,"joining_grind_paths",||crate::exposed_edge_cache::rails(snapshot))?;
    let record=cache_progress::run(operation,"saving_grind_data",||crate::exposed_rail_cache::store(snapshot,&rails,&stitch))?;
    Ok((rails,stitch,serde_json::to_value(record).map_err(|e|e.to_string())?))
}

fn exposed_graph(manifest:&Path,snapshot:&crate::exposed_edge_cache::Snapshot,allow_build:bool,operation:&str)
    ->Result<(crate::grind_path_index::GrindPathIndex,graph::GraphCacheRecord),String> {
    match cache_progress::run(operation,"loading_cached_grind_data",||graph::load(manifest,snapshot)) {
        Ok(cached)=>return Ok(cached),
        Err(error)=>eprintln!("WORLD_INCREMENTAL_GRIND_CACHE unavailable: {error}"),
    }
    if !allow_build {
        // Original prepared DWR files remain valid startup inputs. Their first
        // use with the new graph version is an explicit one-time migration.
        let legacy=cache_progress::run(operation,"loading_cached_grind_data",||crate::exposed_rail_cache::load_with_record(snapshot))?;
        if legacy.is_none(){return Err("Prepared incremental grind index is missing; run offline setup repair before entering the world".into());}
        drop(legacy);
        eprintln!("WORLD_INCREMENTAL_GRIND_CACHE migrating verified native rail cache");
    }
    cache_progress::run(operation,"joining_grind_paths",||graph::build(manifest,snapshot))
}
fn base_bake(manifest:&Path,rail_file:Option<&Path>)->Result<(Arc<CompactWorld>,LipBake,PathBuf),String> {
    let base=cache_progress::run("prepare","collision_load",||CompactWorld::load(manifest).map(Arc::new))?;
    let rail_path=rail_file.map(Path::to_path_buf).unwrap_or_else(||manifest.with_file_name("world.lips"));
    let bake=cache_progress::run("prepare","preparing_legacy_grinds",||Ok(match compact_rails::load(&rail_path,&base) {
        Ok(bake)=>{eprintln!("WORLD_RAIL_CACHE hit lips={}",bake.lips.len());bake},
        Err(error)=>{
            if rail_file.is_some(){return Err(format!("Explicit permanent rail file rejected: {error}"));}
            eprintln!("WORLD_RAIL_CACHE preparing: {error}");
            let view=CompactGeometry::new(base.clone(),[0.;3])?;let bake=compact_rails::bake(&view)?;
            compact_rails::save(&rail_path,&bake)?;bake
        }
    }))?;Ok((base,bake,rail_path))
}
/// Offline setup entry: owned geometry only, no Session, input or Skate assets.
pub fn prepare_offline(manifest:&Path,scene:Option<&Path>,assets:Option<&Path>,exposed_edges:bool)->Result<Value,String> {
    let parameters=if exposed_edges{Some(crate::exposed_edge_cache::Parameters::load(assets.ok_or("Exposed-edge preparation requires owned assets for the original grind probe dimensions")?)?)}else{None};
    let started=Instant::now();let(base,bake,rail_path)=base_bake(manifest,None)?;
    let base_lips=bake.lips.len();let source=cache_progress::run("prepare","collision_load",||source(&base,scene))?;
    if let Some(parameters)=parameters{
        // Preserve the original base fallback once. A current scene uses the
        // derived base/delta directly and never pays for an unused legacy
        // scene lip preparation or global scene merge.
        let merged=cache_progress::run("prepare","preparing_legacy_grinds",||crate::merged_rail_cache::prepare(manifest,&bake))?;
        if merged.path.is_none(){return Err("Offline legacy base rails were not persisted".into());}
        let snapshot=cache_progress::run("prepare","preparing_exposed_cells",||match crate::exposed_edge_cache::load(manifest,&source.world,&parameters){
            Ok(snapshot)=>Ok(snapshot),
            Err(_)=>{
                if scene.is_some(){
                    let prior=crate::exposed_edge_cache::prepare(manifest,&base,&parameters,None,&[])?;
                    crate::exposed_edge_cache::prepare(manifest,&source.world,&parameters,Some(&prior),&source.world.changed_bounds())
                }else{crate::exposed_edge_cache::prepare(manifest,&source.world,&parameters,None,&[])}
            }
        })?;
        let(rails,stitch,rail_cache)=exposed_rails(&snapshot,true,"prepare")?;
        let rail_count=rails.len();drop(rails);
        let(index,graph_record)=exposed_graph(manifest,&snapshot,true,"prepare")?;
        if index.rail_count()!=rail_count{return Err("Offline original and incremental grind rail census differ".into());}
        let graph_cache=graph_record.value()?;
        let mut status=snapshot.status(rail_count);status["stitch"]=stitch;status["rail_cache"]=rail_cache;status["graph_cache"]=graph_cache;
        cache_phase_ready("prepare",started);
        return Ok(json!({"ok":true,"status":"prepared","session_loaded":false,"scope":"whole_world",
            "source_fingerprint":base.source_fingerprint,"base_fingerprint":base.source_fingerprint,"scene_fingerprint":source.world.scene_fingerprint,
            "rail_file":rail_path,"scene_cache":Value::Null,"base_lips":base_lips,"lips":bake.lips.len(),"candidates":bake.candidates,
            "merged_rails":merged.rails.len(),"merged_rail_cache":merged.path,"exposed_edges":status,"elapsed_ms":started.elapsed().as_millis()}));
    }
    let(cache,bake)=cache_progress::run("prepare","preparing_legacy_grinds",||{Ok(if let Some(scene)=scene {
        let key=crate::scene_rail_cache::key(&bake,&source)?;
        let path=crate::scene_rail_cache::path(scene,&key)?;
        let prepared=crate::scene_rail_cache::prepare(&bake,&source,scene)?;
        // Offline setup must leave a reusable file, not only a valid in-memory
        // result that disappears when this short-lived worker exits.
        compact_rails::load_with_identity(&path,&source.world,&key)
            .map_err(|e|format!("Offline scene cache was not persisted: {e}"))?;
        (Some(path),prepared)
    }else{(None,bake)})})?;
    let merged=cache_progress::run("prepare","joining_grind_paths",||crate::merged_rail_cache::prepare(manifest,&bake))?;
    if merged.path.is_none(){return Err("Offline merged rails were not persisted".into());}
    let mut result=json!({"ok":true,"status":"prepared","session_loaded":false,"scope":"whole_world",
        "source_fingerprint":base.source_fingerprint,"base_fingerprint":base.source_fingerprint,"scene_fingerprint":source.world.scene_fingerprint,
        "rail_file":rail_path,"scene_cache":cache,"base_lips":base_lips,"lips":bake.lips.len(),
        "candidates":bake.candidates,"merged_rails":merged.rails.len(),"merged_rail_cache":merged.path,"elapsed_ms":started.elapsed().as_millis()});
    result["elapsed_ms"]=json!(started.elapsed().as_millis());cache_phase_ready("prepare",started);Ok(result)
}
impl WorldRuntime {
    pub fn init(assets:&Path,manifest:&Path,scene:Option<&Path>,rail_file:Option<&Path>,scene_cache_required:bool,spawn:[f32;3],heading:f32)
        ->Result<(Session,Self),String> {
        if scene_cache_required&&scene.is_none(){return Err("Required scene cache needs a scene manifest".into());}
        let parameters=crate::exposed_edge_cache::Parameters::load(assets)?;
        let(base,initial_source)=cache_progress::run("init","collision_load",||{
            let base=Arc::new(CompactWorld::load(manifest)?);let initial_source=source(&base,scene)?;Ok((base,initial_source))
        })?;
        // An exact derived scene, or a complete derived base plus bounded host
        // changes, is selected before the legacy scene lip/merge machinery.
        if let Some((exposed,resident))=cache_progress::run("init","preparing_exposed_cells",||Self::derived(manifest,&base,&initial_source,&parameters,scene_cache_required))?{
            let resident=Arc::new(resident);
            let(mut index,graph_record)=exposed_graph(manifest,&exposed,exposed.stats["cache_hit"].as_bool()!=Some(true),"init")?;
            index.rebind_initial_owners()?;
            let rail_count=index.rail_count();let stitch=index.stats();let rail_cache=graph_record.value()?;
            let rails=cache_progress::run("init","loading_cached_grind_data",||Ok(index.rails()))?;
            let session=cache_progress::run("init","building_runtime_provider",||Session::new_with_source_exposed(assets,initial_source.clone(),rails,spawn,heading))?;
            let mut status=Self::describe(&base,&initial_source,rail_count);
            status["exposed_edges"]=exposed.status(rail_count);status["exposed_edges"]["stitch"]=stitch;status["exposed_edges"]["rail_cache"]=rail_cache.clone();status["exposed_edges"]["graph_cache"]=rail_cache.clone();status["exposed_edges"]["candidate_admission"]=json!("nearest40_then_native_order");
            if let Some(mut checkpoint)=exposed.checkpoint(){checkpoint["rail_cache"]=rail_cache.clone();checkpoint["graph_cache"]=rail_cache;status["scene_checkpoint"]=checkpoint;}
            let graph=Some(graph::GraphState::new(index,graph_record,true));
            return Ok((session,Self{base,bake:None,source:initial_source,manifest:manifest.into(),parameters,exposed:Some(Arc::new(exposed)),graph,resident:Some(resident),rail_count,status}));
        }
        let cached_base=manifest.with_file_name("world.lips");
        let rail_file=if scene_cache_required{Some(rail_file.unwrap_or(&cached_base))}else{rail_file};
        let started=Instant::now();
        let source=initial_source;
        let (bake,rails,checkpoint)=cache_progress::run("init","loading_cached_grind_data",||{
        let bake=compact_rails::load(rail_file.unwrap_or(&cached_base),&base)
            .map_err(|e|format!("No complete exposed cache and the legacy fallback needs offline preparation: {e}"))?;
        let accepted=scene.and_then(|scene|match crate::legacy_cache_read::accepted(scene,&source){
            Ok(cached)=>Some(cached),Err(error)=>{eprintln!("WORLD_ACCEPTED_SCENE_CACHE unavailable: {error}");None}});
        let (bake,rails,checkpoint)=if let Some(cached)=accepted {(Arc::new(cached.bake),cached.rails,Some(cached.checkpoint))}else{
        let bake=Arc::new(if let Some(scene)=scene {
            if scene_cache_required {
                let key=crate::scene_rail_cache::key(&bake,&source)?;let path=crate::scene_rail_cache::path(scene,&key)?;
                let cached=compact_rails::load_with_identity(&path,&source.world,&key)
                    .map_err(|e|format!("Required scene cache rejected: {e}"))?;
                eprintln!("WORLD_SCENE_LIP_CACHE required hit key={key} lips={}",cached.lips.len());cached
            }else{
                let key=crate::scene_rail_cache::key(&bake,&source)?;let path=crate::scene_rail_cache::path(scene,&key)?;
                compact_rails::load_with_identity(&path,&source.world,&key)
                    .map_err(|e|format!("Broader grind coverage and current legacy scene cache need offline preparation: {e}"))?
            }
        }else{bake});
        let rails=crate::legacy_cache_read::merged(manifest,&bake)?;
        (bake,rails,None)};
        Ok((bake,rails,checkpoint))})?;
        let(rails,conditioning)=cache_progress::run("init","joining_grind_paths",||condition_rails(&source,scene,rails))?;
        let rail_count=rails.len();let session=cache_progress::run("init","building_runtime_provider",||Session::new_with_source(assets,source.clone(),rails,spawn,heading))?;
        eprintln!("WORLD_SESSION_READY triangles={} rails={} elapsed_ms={}",source.world.triangle_count(),rail_count,started.elapsed().as_millis());
        let mut status=Self::describe(&base,&source,rail_count);
        status["exposed_edges"]=json!({"schema":1,"ready":false,"reason":"complete_derived_cache_unavailable","legacy_coverage":true});
        if !conditioning.is_null(){status["rail_conditioning"]=conditioning;}
        if let Some(checkpoint)=checkpoint{status["scene_checkpoint"]=checkpoint;}
        Ok((session,Self{base,bake:Some(bake),source,manifest:manifest.into(),parameters,exposed:None,graph:None,resident:None,rail_count,status}))
    }
    fn derived(manifest:&Path,base:&Arc<CompactWorld>,source:&Arc<CompactGeometry>,parameters:&crate::exposed_edge_cache::Parameters,required:bool)
        ->Result<Option<(crate::exposed_edge_cache::Snapshot,crate::exposed_resident::Resident)>,String>{
        if let Ok(snapshot)=crate::exposed_resident::Resident::load(manifest,&source.world,parameters){return Ok(Some(snapshot));}
        if required{return Ok(None);}
        if source.world.scene_fingerprint.is_some(){
            if let Ok(prior)=crate::exposed_edge_cache::load(manifest,base,parameters){
                let snapshot=crate::exposed_edge_cache::prepare(manifest,&source.world,parameters,Some(&prior),&source.world.changed_bounds())?;
                let resident=crate::exposed_resident::Resident::new(&snapshot)?;
                return Ok(Some((snapshot,resident)));
            }
        }Ok(None)
    }
    pub fn scene(&self,builder:CollisionBuilder,path:&Path)->Result<(Option<PreparedCollision>,Self),String> {
        let started=Instant::now();let source=cache_progress::run("scene","collision_load",||source(&self.base,Some(path)))?;
        if source.world.scene_fingerprint==self.source.world.scene_fingerprint {
            // The exporter fingerprints exact mesh/instance/package content,
            // excluding capture timestamps/revisions. All files were validated
            // above. Retain the actual Session world and grind provider as-is.
            eprintln!("WORLD_SCENE_UNCHANGED elapsed_ms={}",started.elapsed().as_millis());
            return Ok((None,self.clone()));
        }
        let difference=source.world.difference_bounds(&self.source.world)?;
        eprintln!("WORLD_SCENE_DIFFERENCE bounds={}",difference.len());
        if let Some(previous)=&self.exposed{
            let resident=self.resident.as_ref().ok_or("Changed scene needs the verified resident exposed payloads")?;
            let(exposed,resident)=cache_progress::run("scene","preparing_exposed_cells",||resident.prepare(&self.manifest,&source.world,&self.parameters,previous,&difference))?;
            let accepted_graph=self.graph.as_ref().ok_or("Changed scene requires an incremental grind graph")?;
            let mut lease=accepted_graph.take(&previous.manifest_sha256)?;
            let inputs=graph::inputs(&exposed)?;
            let transaction=cache_progress::run("scene","joining_grind_paths",||lease.index.as_mut().ok_or("Missing leased grind graph")?
                .begin(&previous.manifest_sha256,exposed.manifest_sha256.clone(),&inputs))?;
            lease.pending=Some(transaction);
            let delta=std::mem::take(&mut lease.pending.as_mut().ok_or("Missing grind graph transaction")?.delta);
            let collision=cache_progress::run("scene","building_runtime_provider",||builder.build_source_exposed_delta(source.clone(),ExposedRailDelta{
                retired_owners:delta.retired_owners,rails:delta.rails,
            }))?;
            let index=lease.index.as_ref().ok_or("Missing leased grind graph")?;
            let rail_count=index.rail_count();let mut stitch=index.stats();let compacting=index.delta_depth()>=128;
            stitch["graph_compacted"]=json!(compacting);
            let graph_path=graph::delta_location(&self.manifest,&exposed,&accepted_graph.cache)?;
            cache_progress::run("scene","saving_grind_data",||{
                let transaction=lease.pending.as_ref().ok_or("Missing grind graph transaction")?;
                let index=lease.index.as_mut().ok_or("Missing leased grind graph")?;
                if compacting {index.store_proposed(&graph_path,&accepted_graph.cache.algorithm_sha256,transaction)}
                else {index.store_delta(&graph_path,&accepted_graph.cache.path,transaction,&accepted_graph.cache.algorithm_sha256)}
            })?;
            let graph_record=graph::record(&graph_path,&exposed,lease.index.as_ref().ok_or("Missing leased grind graph")?,&accepted_graph.cache.algorithm_sha256,false)?;
            let rail_cache=graph_record.value()?;
            let mut status=Self::describe(&self.base,&source,rail_count);
            status["exposed_edges"]=exposed.status(rail_count);status["exposed_edges"]["stitch"]=stitch;status["exposed_edges"]["rail_cache"]=rail_cache.clone();status["exposed_edges"]["graph_cache"]=rail_cache.clone();status["exposed_edges"]["candidate_admission"]=json!("nearest40_then_retained_native_overlay_order");
            if let Some(mut checkpoint)=exposed.checkpoint(){checkpoint["rail_cache"]=rail_cache.clone();checkpoint["graph_cache"]=rail_cache;status["scene_checkpoint"]=checkpoint;}
            let transaction=lease.pending.take().ok_or("Missing grind graph transaction")?;
            if let Err(error)=lease.index.as_mut().ok_or("Missing leased grind graph")?.commit(transaction){lease.index=None;return Err(error);}
            let index=lease.detach()?;
            eprintln!("WORLD_EXPOSED_SCENE_READY rails={rail_count} elapsed_ms={}",started.elapsed().as_millis());
            return Ok((Some(collision),Self{base:self.base.clone(),bake:None,source,manifest:self.manifest.clone(),parameters:self.parameters.clone(),exposed:Some(Arc::new(exposed)),
                graph:Some(graph::GraphState::new(index,graph_record,false)),resident:Some(Arc::new(resident)),rail_count,status}));
        }
        Err("Changed scene requires the complete exposed-edge cache; run offline repair before updating the legacy fallback".into())
    }

    pub fn status(&self)->Value {self.status.clone()}
    fn describe(base:&CompactWorld,source:&CompactGeometry,rail_count:usize)->Value {
        let mut lo=[f64::INFINITY;3];let mut hi=[f64::NEG_INFINITY;3];
        for group in source.world.groups(){for i in 0..3 {lo[i]=lo[i].min(group.bounds.min[i]);hi[i]=hi[i].max(group.bounds.max[i]);}}
        json!({"scope":"whole_world","anchor":[0,0,0],"bounds_unreal":{"min":lo,"max":hi},
            "source_fingerprint":base.source_fingerprint,"scene_fingerprint":source.world.scene_fingerprint,
            "active_triangles":source.world.counts.static_triangles+source.world.counts.terrain_triangles,
            "triangle_ordinals":source.world.triangle_count(),"grinding_ready":true,"rails":rail_count})
    }
}
