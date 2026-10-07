#[path="../src/building_rail_conditioner.rs"]mod conditioner;
use conditioner::{Rails,Triangle,TARGET};
use serde_json::{Value,json};
use sha2::{Digest,Sha256};
use std::{fs,path::PathBuf,sync::atomic::{AtomicUsize,Ordering},time::{SystemTime,UNIX_EPOCH}};
static COUNT:AtomicUsize=AtomicUsize::new(0);
struct Fixture{root:PathBuf,tris:Vec<Triangle>,translations:Vec<[f64;3]>,matrices:Vec<[[f64;3];3]>,package:String}
fn box_triangles(lo:[f64;3],hi:[f64;3])->Vec<Triangle>{
 let p:Vec<[f64;3]>=(0..8).map(|n|std::array::from_fn(|a|if n&(1<<a)==0{lo[a]}else{hi[a]})).collect();
 [[0,4,6],[0,6,2],[1,3,7],[1,7,5],[0,1,5],[0,5,4],[2,6,7],[2,7,3],[0,2,3],[0,3,1],[4,5,7],[4,7,6]].into_iter().map(|t|t.map(|i|p[i])).collect()
}
fn generic_geometry()->Vec<Triangle>{
 [([-150.,-24.,245.],[150.,26.,297.]),([-154.,-24.,0.],[-126.,26.,300.]),([126.,-24.,0.],[154.,26.,300.])]
 .into_iter().flat_map(|(a,b)|box_triangles(a,b)).collect()
}
impl Fixture{
 fn new(tris:Vec<Triangle>,translations:Vec<[f64;3]>)->Self{
  let id=SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
  let root=std::env::temp_dir().join(format!("skate-conditioner-{}-{id}-{}",std::process::id(),COUNT.fetch_add(1,Ordering::SeqCst)));
  fs::create_dir(&root).unwrap();let matrices=vec![[[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]];translations.len()];
  Self{root,tris,translations,matrices,package:"/SkateRuntime/Buildings/fixture/Lightweight.Lightweight".into()}
 }
 fn generic()->Self{Self::new(generic_geometry(),(0..6).map(|i|[16000.+i as f64*300.,184000.,-3400.]).collect())}
 fn transformed(&self,index:usize)->Vec<Triangle>{self.tris.iter().map(|t|t.map(|p|std::array::from_fn(|i|self.translations[index][i]+(0..3).map(|j|self.matrices[index][i][j]*p[j]).sum::<f64>()))).collect()}
 fn world(&self)->Vec<Triangle>{(0..self.translations.len()).flat_map(|i|self.transformed(i)).collect()}
 fn write(&self)->PathBuf{
  let geometry:Vec<u8>=self.tris.iter().flatten().flatten().flat_map(|v|v.to_le_bytes()).collect();let mut instances=vec![];
  for i in 0..self.translations.len(){instances.extend(0u32.to_le_bytes());instances.extend(0u32.to_le_bytes());
   instances.extend(self.matrices[i].iter().flatten().chain(self.translations[i].iter()).flat_map(|v|v.to_le_bytes()));
   let t=self.transformed(i);for bound in [false,true]{for a in 0..3{
    let v=if bound{t.iter().flatten().map(|p|p[a]).fold(f64::NEG_INFINITY,f64::max)}else{t.iter().flatten().map(|p|p[a]).fold(f64::INFINITY,f64::min)};
    instances.extend(v.to_le_bytes());
   }}
  }
  let file=|name:&str,b:&[u8]|{fs::write(self.root.join(name),b).unwrap();json!({"path":name,"bytes":b.len(),"sha256":Sha256::digest(b).iter().map(|b|format!("{b:02x}")).collect::<String>()})};
  let m=json!({"magic":"S3O1","schema":1,"source_fingerprint":"fixture","geometry_base_count":0,
   "geometry":[{"path":TARGET,"offset":0,"count":self.tris.len(),"unsupported":0}],"geometry_file":file("geometry.f64",&geometry),
   "instance_file":file("instances.bin",&instances),"instance_count":self.translations.len(),"instance_stride":152,
   "instance_packages":[self.package],"packages":[self.package],"completeness":{"complete":true}});
  let path=self.root.join("manifest.json");fs::write(&path,serde_json::to_vec(&m).unwrap()).unwrap();path
 }
 fn rails(&self)->Rails{
  let first=self.translations[0];let last=*self.translations.last().unwrap();
  [-24.,26.].into_iter().map(|side|vec![[(first[0]-150.)as f32/100.,(first[2]+297.)as f32/100.,-((first[1]+side)as f32/100.)],
   [(last[0]+150.)as f32/100.,(first[2]+297.)as f32/100.,-((first[1]+side)as f32/100.)]]).collect()
 }
 fn condition(&self,rails:Rails,extra:&[Triangle])->(Rails,conditioner::Report){
  let path=self.write();let mut world=self.world();world.extend_from_slice(extra);
  conditioner::condition(&path,"fixture",rails,|_,_|world.clone()).unwrap()
 }
}
impl Drop for Fixture{fn drop(&mut self){
 let parent=std::env::temp_dir().canonicalize().unwrap();
 if let Ok(root)=self.root.canonicalize(){assert_eq!(root.parent(),Some(parent.as_path()));assert!(root.file_name().unwrap().to_str().unwrap().starts_with("skate-conditioner-"));fs::remove_dir_all(root).unwrap();}
}}
#[test]fn supported_straight_rails_raised_in_place_and_physics_unchanged(){
 let f=Fixture::generic();let rails=f.rails();let path=f.write();let physics=fs::read(path.with_file_name("geometry.f64")).unwrap();let world=f.world();
 let (got,r)=conditioner::condition(&path,"fixture",rails.clone(),|_,_|world.clone()).unwrap();assert_eq!(r.raised_rails,2);assert_eq!(got.len(),rails.len());
 for (a,b)in got.iter().zip(&rails){for (a,b)in a.iter().zip(b){assert_eq!((a[0],a[2]),(b[0],b[2]));assert_eq!(a[1],-31.);}}
 assert_eq!(physics,fs::read(path.with_file_name("geometry.f64")).unwrap());assert_eq!(world,f.world());
 let (again,r)=f.condition(got.clone(),&[]);assert_eq!(again,got);assert_eq!(r.raised_rails,0);
}
#[test]fn true_support_gap_is_not_bridged(){
 let mut f=Fixture::generic();let rails=f.rails();f.translations[3][0]+=10.;let (got,r)=f.condition(rails.clone(),&[]);
 assert_eq!(got,rails);assert_eq!(r.raised_rails,0);assert_eq!(r.rejected_support,2);
}
#[test]fn rotated_scaled_stacked_and_corner_layouts_rejected(){
 for mode in 0..4{let mut f=Fixture::generic();let rails=f.rails();match mode{
  0=>f.matrices[2]=[[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]],
  1=>f.matrices[2][0][0]=1.01,
  2=>{f.translations.push([16600.,184000.,-3100.]);f.matrices.push(f.matrices[0]);},
  _=>f.translations[2][1]+=5.,
 }let (got,r)=f.condition(rails.clone(),&[]);assert_eq!(got,rails,"case{mode}");assert_eq!(r.raised_rails,0);}
 let f=Fixture::generic();let mut rails=f.rails();let p=*rails[0].last().unwrap();rails[0].push([p[0],p[1],p[2]+1.]);
 let (got,r)=f.condition(rails.clone(),&[]);assert_eq!(got[0],rails[0]);assert_eq!(r.raised_rails,1);
}
#[test]fn full_span_clearance_catches_small_unsampled_obstructions(){
 let f=Fixture::generic();let rails=f.rails();let y=f.translations[0][1]-24.;let z=f.translations[0][2]+300.;
 for extra in [box_triangles([16432.,y-9.01,z-0.5],[16432.1,y-8.99,z+0.5]),
  box_triangles([16732.,y-0.2,z+8.99],[16732.1,y+0.2,z+9.01])]{
  let (got,r)=f.condition(rails.clone(),&extra);assert_eq!(got[0],rails[0]);assert_eq!(r.rejected_obstacles,1);assert_eq!(r.raised_rails,1);
 }
}
#[test]fn unrelated_scope_geometry_and_rails_preserved(){
 let mut f=Fixture::generic();let rails=f.rails();f.package="/Game/World.World".into();assert_eq!(f.condition(rails.clone(),&[]).0,rails);
 f.package="/SkateRuntime/Buildings/fixture/Lightweight.Lightweight".into();f.tris[0][0][0]+=0.1;assert_eq!(f.condition(rails.clone(),&[]).0,rails);
 let f=Fixture::generic();let rails=vec![vec![[0.,0.,0.],[20.,0.,0.]]];let (got,r)=f.condition(rails.clone(),&[]);assert_eq!(got,rails);assert_eq!(r.raised_rails,0);
 let mut f=Fixture::generic();for t in &mut f.tris[..12]{for p in t{if p[2]==297.{p[2]=296.7;}}}let rails=f.rails();assert_eq!(f.condition(rails.clone(),&[]).0,rails);
}
#[test]fn f32_probe_boundary_rounding_is_conservatively_enclosed(){
 let f=Fixture::generic();let rails=f.rails();let y=f.translations[0][1]-24.;let z=f.translations[0][2]+300.;
 // Entire obstacle is above the old exact-f64 raised box's z+9.1 limit,
 // but lies inside the f32 coordinate/offset uncertainty envelope.
 let obstacle=box_triangles([16432.,y-0.02,z+9.14],[16432.1,y+0.02,z+9.16]);
 assert!(obstacle.iter().flatten().all(|p|p[2]>z+9.1));
 let (got,r)=f.condition(rails.clone(),&obstacle);
 assert_eq!(got[0],rails[0]);assert_eq!(r.rejected_obstacles,1);assert_eq!(r.raised_rails,1);
}
#[test]fn altered_inputs_and_wrong_identity_rejected(){
 let f=Fixture::generic();let path=f.write();assert!(conditioner::condition(&path,"wrong",f.rails(),|_,_|vec![]).is_err());
 let file=path.with_file_name("geometry.f64");let mut bytes=fs::read(&file).unwrap();bytes[0]^=1;fs::write(file,bytes).unwrap();
 assert!(conditioner::condition(&path,"fixture",f.rails(),|_,_|vec![]).is_err());
}

fn ray_hit(start:[f32;4],end:[f32;4],tris:&[Triangle])->Option<skate_core::air::trajectory::grind_surface::ProbeHit>{
 use skate_core::air::trajectory::grind_surface::ProbeHit;
 let start3=[start[0]as f64,start[1]as f64,start[2]as f64];let delta=[(end[0]-start[0])as f64,(end[1]-start[1])as f64,(end[2]-start[2])as f64];
 let sub=|a:[f64;3],b:[f64;3]|std::array::from_fn(|i|a[i]-b[i]);
 let cross=|a:[f64;3],b:[f64;3]|[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
 let dot=|a:[f64;3],b:[f64;3]|(0..3).map(|i|a[i]*b[i]).sum::<f64>();let mut best=None;let mut limit=1.;
 for t in tris{let t=t.map(|p|[p[0]*0.01,p[2]*0.01,-p[1]*0.01]);let e1=sub(t[1],t[0]);let e2=sub(t[2],t[0]);let p=cross(delta,e2);let det=dot(e1,p);if det.abs()<1e-15{continue;}
  let s=sub(start3,t[0]);let u=dot(s,p)/det;let q=cross(s,e1);let v=dot(delta,q)/det;let fraction=dot(e2,q)/det;
  if u>=0.&&v>=0.&&u+v<=1.&&fraction>=0.&&fraction<=limit{limit=fraction;let n=cross(e1,e2);let len=dot(n,n).sqrt();
   best=Some(ProbeHit{fraction:fraction as f32,position:[(start3[0]+delta[0]*fraction)as f32,(start3[1]+delta[1]*fraction)as f32,(start3[2]+delta[2]*fraction)as f32,0.],normal:[(n[0]/len)as f32,(n[1]/len)as f32,(n[2]/len)as f32,0.],packed_surface:128});
  }
 }best
}
#[test]fn original_native_probes_accept_raw_and_raised_synthetic_run(){
 use skate_core::air::trajectory::grind_surface::{self,InvestigationInput,GeometryType};
 let f=Fixture::generic();let raw=f.rails();let (raised,r)=f.condition(raw.clone(),&[]);assert_eq!(r.raised_rails,2);let world=f.world();
 for rails in [raw,raised]{for rail in rails{
  let a=rail[0];let b=*rail.last().unwrap();let v4=|p:[f32;3]|[p[0],p[1],p[2],0.];
  for station in 0..=120{let t=station as f32/120.;let reference=std::array::from_fn(|i|a[i]+(b[i]-a[i])*t);
   let input=InvestigationInput{start:v4(a),end:v4(b),reference:v4(reference),optional_probe:None,deck_center_to_truck:0.2};
   let result=grind_surface::investigate::<()>(input,|_,p|Ok(ray_hit(p.start,p.end,&world))).unwrap();
   assert_eq!(result.kind,GeometryType::Ledge,"station{station}");assert_eq!(result.flags&grind_surface::GrindSurface::BLOCKED_CROSS_SECTION,0);
  }
 }}
}
#[test]
#[ignore="requires user's private recorded doorway fixture; no game data is bundled"]
fn recorded_six_doorframes_and_original_probe_acceptance(){
 let file=std::env::var("SKATE_DOORFRAME_FIXTURE").expect("Provide private recorded fixture path");let v:Value=serde_json::from_slice(&fs::read(file).unwrap()).unwrap();
 let tris:Vec<Triangle>=serde_json::from_value(v["triangles_cm"].clone()).unwrap();
 let translations=v["frames"].as_array().unwrap().iter().map(|f|{let t=&f["transform"]["Translation"];[t["X"].as_f64().unwrap(),t["Y"].as_f64().unwrap(),t["Z"].as_f64().unwrap()]}).collect();
 let f=Fixture::new(tris,translations);let rails:Rails=v["nearby_rails"].as_array().unwrap().iter().map(|r|serde_json::from_value(r["points"].clone()).unwrap()).collect();
 let (got,report)=f.condition(rails.clone(),&[]);assert_eq!(report.raised_rails,2);assert_eq!(got.len(),rails.len());let world=f.world();
 use skate_core::air::trajectory::grind_surface::{self,InvestigationInput,GeometryType};
 let mut stations=0;
 for (rail,old)in got.iter().zip(&rails){if rail==old{continue;}
  assert!(report.maximum_rise_cm>3.15&&report.maximum_rise_cm<3.16);
  let a=rail[0];let b=*rail.last().unwrap();let v4=|p:[f32;3]|[p[0],p[1],p[2],0.];
  let mut samples:Vec<f32>=(0..=360).map(|i|i as f32/360.).collect();
  for join in 1..6{for offset in [-0.00001,0.,0.00001]{samples.push(join as f32/6.+offset);}}
  for t in samples{let reference=std::array::from_fn(|i|a[i]+(b[i]-a[i])*t);
   let input=InvestigationInput{start:v4(a),end:v4(b),reference:v4(reference),optional_probe:None,deck_center_to_truck:0.2};
   let result=grind_surface::investigate::<()>(input,|_,p|Ok(ray_hit(p.start,p.end,&world))).unwrap();
   assert_eq!(result.kind,GeometryType::Ledge,"station{t}");assert_eq!(result.flags&grind_surface::GrindSurface::BLOCKED_CROSS_SECTION,0);stations+=1;
  }
 }
 eprintln!("RECORDED_CONDITIONING raised={} source_probe_stations={stations} maximum_rise_cm={}",report.raised_rails,report.maximum_rise_cm);
}
