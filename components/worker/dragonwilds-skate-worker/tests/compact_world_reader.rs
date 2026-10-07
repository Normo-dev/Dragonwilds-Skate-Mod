#[path="../src/compact_world.rs"]
mod compact_world;
use compact_world::{Bounds,BoundsIndex,CompactWorld};
use std::{fs,path::PathBuf,time::Instant};

#[cfg(windows)]
fn memory()->(usize,usize,usize){
    #[repr(C)]struct Counters{cb:u32,faults:u32,values:[usize;9]}
    #[link(name="psapi")]unsafe extern "system"{fn GetProcessMemoryInfo(process:*mut std::ffi::c_void,counters:*mut Counters,size:u32)->i32;}
    let mut counters=Counters{cb:std::mem::size_of::<Counters>() as u32,faults:0,values:[0;9]};
    assert_ne!(unsafe{GetProcessMemoryInfo((-1isize) as *mut _,&mut counters,std::mem::size_of::<Counters>() as u32)},0);
    (counters.values[1],counters.values[0],counters.values[8])
}

#[test]
fn compact_fixture_matches_exported_order_mirrors_holes_and_brute_force_queries(){
    let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../compact-world-fixture");
    let world=CompactWorld::load(&root.join("export/manifest.json")).unwrap();
    let expected=fs::read(root.join("expected.f64")).unwrap();
    assert_eq!(expected.len(),world.triangle_count()*72);
    for id in 0..world.triangle_count(){let actual=world.raw_triangle(id);
        for (i,v) in actual.iter().flatten().enumerate(){let at=id*72+i*8;let original=f64::from_le_bytes(expected[at..at+8].try_into().unwrap());
            assert!((v-original).abs()<1e-10,"triangle{id} element{i}: {v} != {original}");}}
    let mut seed=734u32;let mut random=||{seed=seed.wrapping_mul(1664525).wrapping_add(1013904223);seed as f64/u32::MAX as f64};
    for _ in 0..300 {let min=std::array::from_fn(|_|random()*2400.-1200.);let max=std::array::from_fn(|i|min[i]+random()*300.);
        let expected:Vec<_>=(0..world.triangle_count()).filter(|&id|{let tri=world.raw_triangle(id);
            (0..3).all(|axis|tri.iter().map(|p|p[axis]).fold(f64::INFINITY,f64::min)<=max[axis]
                &&tri.iter().map(|p|p[axis]).fold(f64::NEG_INFINITY,f64::max)>=min[axis])}).collect();
        assert_eq!(world.candidate_triangles(min,max),expected);}
    for group in world.groups(){let id=group.triangle_range.start;let vertex=world.raw_triangle(id)[0];
        assert!(world.candidate_triangles(vertex,vertex).contains(&id),"exact boundary triangle{id} was omitted");}
    for id in 0..560{
        let neighbors=world.terrain_neighbors(id);assert!(neighbors.contains(&id)&&neighbors.len()<=18);
        assert!(neighbors.iter().all(|&n|n<560));
    }
    assert!(world.terrain_neighbors(560).is_empty());
}

#[test]
fn live_overlay_adds_new_geometry_and_deletes_only_observed_static_packages(){
    let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../compact-world-fixture");
    let base=std::sync::Arc::new(CompactWorld::load(&root.join("export/manifest.json")).unwrap());
    let add=CompactWorld::with_overlay(&base,&root.join("overlay-add/manifest.json")).unwrap();
    assert_eq!(add.triangle_count(),base.triangle_count()+24);
    assert_eq!(add.raw_triangle(1000),base.raw_triangle(1000)); // Stable base ordinals, even while masked.
    let retained=add.candidate_triangles([-2000.;3],[2000.;3]);
    assert_eq!(retained.len(),560);assert!(retained.iter().all(|&id|id<560));
    for x in [5000.,6000.] {let ids=add.candidate_triangles([x-100.,4900.,0.],[x+100.,5100.,200.]);
        assert_eq!(ids.len(),12);assert!(ids.iter().all(|&id|id>=base.triangle_count()));}
    let add=std::sync::Arc::new(add);
    assert_eq!(add.terrain_neighbors(100),base.terrain_neighbors(100));
    assert!(!add.changed_bounds().is_empty());
    assert!(std::sync::Arc::ptr_eq(&add.pristine_base(),&base));
    for name in ["overlay-delete","overlay-disabled"]{
        let removed=CompactWorld::with_overlay(&add,&root.join(name).join("manifest.json")).unwrap();
        assert!(removed.candidate_triangles([4900.,4900.,0.],[6100.,5100.,200.]).is_empty());
        assert_eq!(removed.candidate_triangles([-2000.;3],[2000.;3]).len(),560);
        assert_eq!(removed.counts.static_triangles,0);
    }
    assert!(std::sync::Arc::strong_count(&base)>=2);
}

#[test]
fn previous_view_delta_ignores_unchanged_packages_despite_shifted_live_ordinals(){
    let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../compact-world-fixture");
    let base=std::sync::Arc::new(CompactWorld::load(&root.join("export/manifest.json")).unwrap());
    let add=CompactWorld::with_overlay(&base,&root.join("overlay-add/manifest.json")).unwrap();
    let two=CompactWorld::with_overlay(&base,&root.join("overlay-two/manifest.json")).unwrap();
    let delta=two.difference_bounds(&add).unwrap();
    assert_eq!(delta.len(),1);assert!(delta[0].min[0]>9900.);
    let moved=CompactWorld::with_overlay(&base,&root.join("overlay-two-moved/manifest.json")).unwrap();
    let delta=moved.difference_bounds(&two).unwrap();
    assert_eq!(delta.len(),2);assert!(delta[0].min[0]>9900.&&delta[0].max[0]<10100.);
    assert!(delta[1].min[0]>19900.);
    assert!(moved.difference_bounds(&moved).unwrap().is_empty());
    let reordered=CompactWorld::with_overlay(&base,&root.join("overlay-two-reordered/manifest.json")).unwrap();
    assert_eq!(reordered.difference_bounds(&two).unwrap().len(),6);
    let removed=CompactWorld::with_overlay(&base,&root.join("overlay-delete/manifest.json")).unwrap();
    let disabled=CompactWorld::with_overlay(&base,&root.join("overlay-disabled/manifest.json")).unwrap();
    assert!(removed.difference_bounds(&disabled).unwrap().is_empty());
    assert!(!add.difference_bounds(&base).unwrap().is_empty());
}

#[test]
fn moving_one_instance_does_not_invalidate_its_unchanged_package_neighbors(){
    use sha2::{Digest,Sha256};
    let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../compact-world-fixture");
    let base=std::sync::Arc::new(CompactWorld::load(&root.join("export/manifest.json")).unwrap());
    let scene=root.join("overlay-add");let before=CompactWorld::with_overlay(&base,&scene.join("manifest.json")).unwrap();
    let temporary=root.join(format!("instance-delta-test-{}",std::process::id()));fs::create_dir_all(&temporary).unwrap();
    fs::copy(scene.join("geometry.f64"),temporary.join("geometry.f64")).unwrap();
    let original=fs::read(scene.join("instances.bin")).unwrap();
    let manifest:serde_json::Value=serde_json::from_slice(&fs::read(scene.join("manifest.json")).unwrap()).unwrap();
    let write=|bytes:&[u8],count:usize|{
        let mut m=manifest.clone();m["instance_count"]=count.into();m["triangle_count"]=(count*12).into();
        let digest:String=Sha256::digest(bytes).iter().map(|b|format!("{b:02x}")).collect();
        m["instance_file"]["bytes"]=bytes.len().into();m["instance_file"]["sha256"]=digest.clone().into();
        m["source_fingerprint"]=digest.into();
        fs::write(temporary.join("instances.bin"),bytes).unwrap();
        fs::write(temporary.join("manifest.json"),serde_json::to_vec(&m).unwrap()).unwrap();
        CompactWorld::with_overlay(&base,&temporary.join("manifest.json")).unwrap()
    };
    let mut bytes=original.clone();
    // Translation and both cached bound X coordinates of the second instance.
    for offset in [80,104,128]{let at=152+offset;let x=f64::from_le_bytes(bytes[at..at+8].try_into().unwrap());bytes[at..at+8].copy_from_slice(&(x+20.).to_le_bytes());}
    let moved=write(&bytes,2);let delta=moved.difference_bounds(&before).unwrap();
    assert_eq!(delta.len(),2);assert!(delta.iter().all(|b|b.min[0]>5900.));
    let added=write(&[original.as_slice(),&original[152..]].concat(),3);
    assert_eq!(added.difference_bounds(&before).unwrap().len(),1);
    let removed=write(&original[..152],1);
    assert_eq!(removed.difference_bounds(&before).unwrap().len(),1);
    let reordered=write(&[&original[152..],&original[..152]].concat(),2);
    assert_eq!(reordered.difference_bounds(&before).unwrap().len(),4);
    let absolute=temporary.canonicalize().unwrap();assert!(absolute.starts_with(root.canonicalize().unwrap()));
    fs::remove_dir_all(absolute).unwrap();
}

#[test]
#[ignore="Reads locally captured scenes supplied through environment variables"]
fn recorded_scene_instance_delta(){
    let manifest=PathBuf::from(std::env::var_os("SKATE_WORLD_MANIFEST").expect("SKATE_WORLD_MANIFEST"));
    let old=PathBuf::from(std::env::var_os("SKATE_PREVIOUS_SCENE").expect("SKATE_PREVIOUS_SCENE"));
    let next=PathBuf::from(std::env::var_os("SKATE_NEXT_SCENE").expect("SKATE_NEXT_SCENE"));
    let base=std::sync::Arc::new(CompactWorld::load(&manifest).unwrap());
    let previous=CompactWorld::with_overlay(&base,&old).unwrap();let current=CompactWorld::with_overlay(&base,&next).unwrap();
    let started=Instant::now();let bounds=current.difference_bounds(&previous).unwrap();let delta_ms=started.elapsed().as_secs_f64()*1000.;
    let regions:Vec<_>=bounds.iter().map(|b|{let scale=b.min.into_iter().chain(b.max).map(f64::abs).fold(1.,f64::max);
        let pad=16.+scale*f64::from(f32::EPSILON)*32.;Bounds{min:b.min.map(|x|x-pad),max:b.max.map(|x|x+pad)}}).collect();
    let old_ids=previous.candidate_triangles_many(&regions);let new_ids=current.candidate_triangles_many(&regions);
    println!("RECORDED_SCENE_DELTA bounds={} delta_ms={delta_ms:.3} old_triangles={} new_triangles={}",bounds.len(),old_ids.len(),new_ids.len());
    if let Ok(count)=std::env::var("SKATE_EXPECTED_BOUND_COUNT"){assert_eq!(bounds.len(),count.parse::<usize>().unwrap());}
}

fn repeated_union(world:&CompactWorld,boxes:&[Bounds])->Vec<usize>{
    let mut found:Vec<_>=boxes.iter().flat_map(|b|world.candidate_triangles(b.min,b.max)).collect();
    found.sort_unstable();found.dedup();found
}

#[test]
fn batched_box_queries_preserve_exact_union_order_holes_mirrors_and_masked_groups(){
    let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../compact-world-fixture");
    let base=std::sync::Arc::new(CompactWorld::load(&root.join("export/manifest.json")).unwrap());
    let add=CompactWorld::with_overlay(&base,&root.join("overlay-add/manifest.json")).unwrap();
    let removed=CompactWorld::with_overlay(&base,&root.join("overlay-delete/manifest.json")).unwrap();
    let reordered=CompactWorld::with_overlay(&base,&root.join("overlay-two-reordered/manifest.json")).unwrap();
    for world in [base.as_ref(),&add,&removed,&reordered] {
        let mut seed=9314u32;let mut random=||{seed=seed.wrapping_mul(1664525).wrapping_add(1013904223);seed as f64/u32::MAX as f64};
        let mut boxes=Vec::new();
        for _ in 0..500 {let min=std::array::from_fn(|_|random()*3000.-1500.);
            boxes.push(Bounds{min,max:std::array::from_fn(|i|min[i]+random()*250.)});}
        // Exact vertices and complete individual groups exercise touching
        // boundaries, transformed/singular instances, and the full-group path.
        for (i,group) in world.groups().iter().enumerate(){
            let p=world.raw_triangle(group.triangle_range.start)[0];boxes.push(Bounds{min:p,max:p});
            if i%7==0{boxes.push(group.bounds);}
        }
        boxes.extend([Bounds{min:[4900.,4900.,0.],max:[6100.,5100.,200.]},
                      Bounds{min:[9900.,4900.,0.],max:[10100.,5100.,200.]}]);
        boxes.extend_from_within(..25); // Repeated boxes must not repeat IDs.
        for batch in boxes.chunks(37) {
            assert_eq!(world.candidate_triangles_many(batch),repeated_union(world,batch));
        }
        let expected=repeated_union(world,&boxes);
        assert_eq!(world.candidate_triangles_many(&boxes),expected);
        boxes.reverse();assert_eq!(world.candidate_triangles_many(&boxes),expected);
        let everything=[Bounds{min:[-1e9;3],max:[1e9;3]}];
        let active:Vec<_>=world.groups().iter().flat_map(|g|g.triangle_range.clone()).collect();
        assert_eq!(world.candidate_triangles_many(&everything),active);
        assert_eq!(world.candidate_triangles_many(&everything),repeated_union(world,&everything));
    }
}

#[test]
fn batched_empty_invalid_and_disconnected_boxes_follow_scalar_query_semantics(){
    let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../compact-world-fixture");
    let world=CompactWorld::load(&root.join("export/manifest.json")).unwrap();
    assert!(world.candidate_triangles_many(&[]).is_empty());
    let invalid=[Bounds{min:[f64::NAN,0.,0.],max:[1.;3]},Bounds{min:[0.;3],max:[f64::INFINITY;3]},
        Bounds{min:[5.;3],max:[-5.;3]}];
    assert!(world.candidate_triangles_many(&invalid).is_empty());
    let mut mixed=invalid.to_vec();mixed.push(Bounds{min:[-100.;3],max:[100.;3]});
    assert_eq!(world.candidate_triangles_many(&mixed),repeated_union(&world,&mixed));
    // A box union spans real terrain between these small disjoint selections;
    // that gap must not be returned merely because it enters the broad union.
    let disconnected=[Bounds{min:[-10.,-10.,-10.],max:[10.,10.,10.]},
        Bounds{min:[330.,330.,-10.],max:[350.,350.,10.]}];
    let exact=repeated_union(&world,&disconnected);
    assert_eq!(world.candidate_triangles_many(&disconnected),exact);
    assert!(exact.len()<world.candidate_triangles([-10.,-10.,-10.],[350.,350.,10.]).len());
}

#[test]
fn bounds_index_containment_requires_one_exact_box_not_union_coverage(){
    let mut boxes=vec![Bounds{min:[0.;3],max:[10.;3]},Bounds{min:[20.;3],max:[30.;3]},
        Bounds{min:[5.;3],max:[15.;3]},Bounds{min:[f64::NAN;3],max:[100.;3]}];
    for i in 0..90{let x=100.+f64::from(i)*10.;boxes.push(Bounds{min:[x;3],max:[x+4.;3]});}
    let index=BoundsIndex::new(&boxes);
    assert!(index.contains_box(boxes[0]));
    assert!(index.contains_box(Bounds{min:[10.;3],max:[10.;3]}));
    assert!(!index.contains_box(Bounds{min:[12.;3],max:[22.;3]}));
    assert!(!index.contains_box(Bounds{min:[1.;3],max:[14.;3]}));
    assert!(!index.contains_box(Bounds{min:[f64::NAN;3],max:[1.;3]}));
    assert!(!index.contains_box(Bounds{min:[1.;3],max:[0.;3]}));
    assert!(!BoundsIndex::new(&[]).contains_box(boxes[0]));
    let mut seed=634u32;let mut random=||{seed=seed.wrapping_mul(1664525).wrapping_add(1013904223);seed as f64/u32::MAX as f64};
    for _ in 0..1000 {
        let min=std::array::from_fn(|_|random()*1100.-10.);let max=std::array::from_fn(|i|min[i]+random()*20.);
        let q=Bounds{min,max};let expected=boxes.iter().any(|b|(0..3).all(|i|b.min[i]<=q.min[i]&&q.max[i]<=b.max[i]));
        assert_eq!(index.contains_box(q),expected);
    }
}

#[test]
#[ignore="Reads locally generated owned world and recorded live overlay; run explicitly"]
fn recorded_live_overlay_retains_unloaded_world_and_terrain(){
    let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
    let base=std::sync::Arc::new(CompactWorld::load(&root.join("dragonwilds-map-data/compact-world-v1/manifest.json")).unwrap());
    let start=Instant::now();
    let view=std::sync::Arc::new(CompactWorld::with_overlay(&base,&root.join("wholeworld-live-benchmark/overlay/manifest.json")).unwrap());
    let elapsed=start.elapsed();
    assert!(std::sync::Arc::ptr_eq(&view.pristine_base(),&base));
    let overlay:serde_json::Value=serde_json::from_slice(&fs::read(root.join("wholeworld-live-benchmark/overlay/manifest.json")).unwrap()).unwrap();
    assert_eq!(view.triangle_count(),base.triangle_count()+overlay["triangle_count"].as_u64().unwrap() as usize);
    assert_eq!(view.counts.terrain_triangles,base.counts.terrain_triangles);
    let active:usize=view.groups().iter().map(|g|g.triangle_range.len()).sum();
    assert_eq!(active,view.counts.static_triangles+view.counts.terrain_triangles);
    let retained:Vec<_>=view.groups().iter().filter(|g|g.triangle_range.start<base.triangle_count()).collect();
    for i in 0..100 {
        let group=retained[i*(retained.len()-1)/99];let id=group.triangle_range.start;
        assert_eq!(view.raw_triangle(id),base.raw_triangle(id));
        let p=view.raw_triangle(id)[0];
        assert!(view.candidate_triangles(p.map(|x|x-0.01),p.map(|x|x+0.01)).contains(&id));
    }
    println!("COMPACT_LIVE_OVERLAY load_ms={:.3} active_triangles={active} ordinal_count={} groups={} changed_bounds={}",
        elapsed.as_secs_f64()*1000.,view.triangle_count(),view.groups().len(),view.changed_bounds().len());
    #[cfg(windows)]{let(ws,peak,private)=memory();println!("COMPACT_LIVE_MEMORY working_set_bytes={ws} peak_working_set_bytes={peak} private_bytes={private}");}
}

#[test]
#[ignore="Reads locally generated owned full-world cache; run explicitly"]
fn load_owned_whole_world_and_query_character_area(){
    let manifest=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../dragonwilds-map-data/compact-world-v1/manifest.json");
    let start=Instant::now();let world=CompactWorld::load(&manifest).unwrap();let elapsed=start.elapsed();
    let min=[27500.,181700.,-4300.];let max=[27800.,182000.,-4100.];let query=Instant::now();let ids=world.candidate_triangles(min,max);
    println!("COMPACT_WORLD triangles={} groups={} load_ms={:.3} query_us={:.3} candidates={} fingerprint={}",
        world.triangle_count(),world.groups().len(),elapsed.as_secs_f64()*1000.,query.elapsed().as_secs_f64()*1e6,ids.len(),world.source_fingerprint);
    let mut timings=Vec::new();let mut max_candidates=0;
    for i in 0..1000{let group=&world.groups()[i*(world.groups().len()-1)/999];let p=world.raw_triangle(group.triangle_range.start)[0];
        let min=p.map(|v|v-100.);let max=p.map(|v|v+100.);let start=Instant::now();let found=world.candidate_triangles(min,max);
        timings.push(start.elapsed().as_secs_f64()*1e6);max_candidates=max_candidates.max(found.len());assert!(found.contains(&group.triangle_range.start));}
    timings.sort_by(f64::total_cmp);
    println!("COMPACT_WORLD_QUERIES count={} mean_us={:.3} p95_us={:.3} max_us={:.3} max_candidates={}",
        timings.len(),timings.iter().sum::<f64>()/timings.len() as f64,timings[949],timings[999],max_candidates);
    #[cfg(windows)]{let(ws,peak,private)=memory();println!("COMPACT_WORLD_MEMORY working_set_bytes={ws} peak_working_set_bytes={peak} private_bytes={private}");}
    let census:serde_json::Value=serde_json::from_slice(&fs::read(&manifest).unwrap()).unwrap();
    assert_eq!(world.triangle_count(),(census["terrain_triangle_count"].as_u64().unwrap()+census["static_triangle_count"].as_u64().unwrap()) as usize);
    assert!(!ids.is_empty());
}
