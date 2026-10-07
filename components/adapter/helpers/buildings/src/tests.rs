use super::*;
use std::cell::Cell;
const BASE:u64=0x100000;
const MANAGER:u64=0x200000;
const DATA:u64=0x300000;
const FIRST:u64=0x400000;
#[derive(Clone)] struct Fixture{blocks:BTreeMap<u64,Vec<u8>>}
impl Memory for Fixture {
    fn read(&self,address:u64,len:usize)->Result<Vec<u8>> {
        for (start,b) in &self.blocks {
            if address>=*start && address+len as u64<=*start+b.len() as u64 {
                return Ok(b[(address-start) as usize..(address-start) as usize+len].to_vec());
            }
        }Err(format!("unmapped {address:x}/{len}"))
    }
}
fn put32(b:&mut[u8],i:usize,v:u32){b[i..i+4].copy_from_slice(&v.to_le_bytes())}
fn put64(b:&mut[u8],i:usize,v:u64){b[i..i+8].copy_from_slice(&v.to_le_bytes())}
fn fixture()->Fixture {
    let mut m=vec![0;1488];put64(&mut m,0,BASE+MANAGER_VTABLE);put64(&mut m,1328,BASE+RESOLVER_VTABLE);
    put64(&mut m,1360,DATA);put32(&mut m,1368,3);put32(&mut m,1372,4);
    put32(&mut m,1376,5);put32(&mut m,1400,3);put32(&mut m,1404,128);
    put32(&mut m,1408,1);put32(&mut m,1412,1);put32(&mut m,1416,2);put32(&mut m,1432,1);
    let mut entries=vec![0xcc;72];
    let mut blocks=BTreeMap::new();
    for (slot,id,pointer,next) in [(0,7,FIRST,u32::MAX),(2,8,FIRST+0x1000,0)] {
        put32(&mut entries,slot*24,id);put64(&mut entries,slot*24+8,pointer);
        put32(&mut entries,slot*24+16,next);put32(&mut entries,slot*24+20,0);
        let mut p=vec![0;688];put64(&mut p,0,BASE+PIECE_VTABLE);put64(&mut p,368,0x500000);put64(&mut p,376,0x600000);
        put64(&mut p,464,MANAGER);put64(&mut p,504,1f64.to_bits());
        for i in [544,552,560] {put64(&mut p,i,1f64.to_bits());}
        put32(&mut p,668,id);p[675]=1;blocks.insert(pointer,p);
    }
    blocks.insert(MANAGER,m);blocks.insert(DATA,entries);Fixture{blocks}
}
#[test]fn sparse_inventory_skips_freed_slots_and_orders_ids(){
    let f=fixture();let out=ids(&f,BASE,MANAGER).unwrap();
    assert_eq!(&out[..7],b"DWBIDS1");assert_eq!(u32at(&out,19),2);
    assert_eq!((u32at(&out,23),u32at(&out,27)),(7,8));assert_eq!(out.len(),31);
}
#[test]fn row_protocol_preserves_requested_order_and_transform(){
    let mut f=fixture();put64(f.blocks.get_mut(&FIRST).unwrap(),512,123.25f64.to_bits());
    let out=rows(&f,BASE,MANAGER,&[8,7]).unwrap();assert_eq!(out.len(),239);
    assert_eq!((u32at(&out,23),u32at(&out,131)),(8,7));
    assert_eq!(f64::from_le_bytes(out[183..191].try_into().unwrap()),123.25);
    assert_eq!(out[234],1);
}
#[test]fn bad_map_metadata_fails_without_traversal(){
    for (offset,value) in [(1368,100001),(1412,4),(1400,2),(1404,129),(1432,2)] {
        let mut f=fixture();put32(f.blocks.get_mut(&MANAGER).unwrap(),offset,value);
        assert!(ids(&f,BASE,MANAGER).is_err(),"offset {offset}");
    }
}
#[test]fn wrong_object_identity_and_entity_bounds_fail(){
    for (offset,value) in [(0,0),(464,MANAGER+8),(668,9),(640,1025)]{
        let mut f=fixture();let b=f.blocks.get_mut(&FIRST).unwrap();
        if offset==668 || offset==640 {put32(b,offset,value as u32)}else{put64(b,offset,value)}
        assert!(rows(&f,BASE,MANAGER,&[7]).is_err());
    }
}
#[test]fn hash_cycles_freed_slots_and_bad_buckets_fail(){
    let mut f=fixture();put32(f.blocks.get_mut(&DATA).unwrap(),2*24+16,2);
    assert!(rows(&f,BASE,MANAGER,&[7]).is_err());
    put32(f.blocks.get_mut(&DATA).unwrap(),2*24+16,1);
    assert!(rows(&f,BASE,MANAGER,&[7]).is_err());
    let mut f=fixture();put32(f.blocks.get_mut(&DATA).unwrap(),2*24+20,1);
    assert!(rows(&f,BASE,MANAGER,&[8]).is_err());
}
#[test]fn changing_piece_is_rejected(){
    struct Changing{f:Fixture,reads:Cell<usize>}
    impl Memory for Changing{fn read(&self,p:u64,n:usize)->Result<Vec<u8>>{
        let mut b=self.f.read(p,n)?;if p==FIRST && n==688 {
            self.reads.set(self.reads.get()+1);if self.reads.get()>1 {b[675]=0;}
        }Ok(b)
    }}
    assert!(rows(&Changing{f:fixture(),reads:Cell::new(0)},BASE,MANAGER,&[7]).is_err());
}
#[test]fn bad_requests_and_missing_ids_fail(){
    let f=fixture();assert!(request(&f,BASE,b"DWBQ1",false).is_err());
    for list in [vec![],vec![7,7],vec![99],vec![7;17]] {assert!(rows(&f,BASE,MANAGER,&list).is_err());}
    let mut q=b"DWBQ1".to_vec();q.extend(MANAGER.to_le_bytes());q.extend(0xffffffffu32.to_le_bytes());
    assert!(request(&f,BASE,&q,true).is_err());
}
#[test]fn invalid_transform_and_flags_fail(){
    for (offset,value) in [(480,f64::NAN.to_bits()),(504,0),(512,f64::INFINITY.to_bits())]{
        let mut f=fixture();put64(f.blocks.get_mut(&FIRST).unwrap(),offset,value);
        assert!(rows(&f,BASE,MANAGER,&[7]).is_err());
    }
    let mut f=fixture();f.blocks.get_mut(&FIRST).unwrap()[672]=2;
    assert!(rows(&f,BASE,MANAGER,&[7]).is_err());
}
#[test]fn duplicate_ids_and_pointers_fail(){
    let mut f=fixture();put32(f.blocks.get_mut(&DATA).unwrap(),48,7);assert!(ids(&f,BASE,MANAGER).is_err());
    let mut f=fixture();put64(f.blocks.get_mut(&DATA).unwrap(),56,FIRST);assert!(ids(&f,BASE,MANAGER).is_err());
}
#[test]fn empty_inventory_is_supported(){
    let mut f=fixture();let m=f.blocks.get_mut(&MANAGER).unwrap();
    for offset in [1368,1372,1400,1412,1432] {put32(m,offset,0)}
    put64(m,1360,0);let out=ids(&f,BASE,MANAGER).unwrap();assert_eq!(out.len(),23);assert_eq!(u32at(&out,19),0);
}
#[test]fn heap_bitmap_and_hash_table_match_inline_results(){
    let expected=rows(&fixture(),BASE,MANAGER,&[7,8]).unwrap();
    let mut f=fixture();let m=f.blocks.get_mut(&MANAGER).unwrap();
    put64(m,1392,0x700000);put32(m,1404,256);put64(m,1424,0x710000);
    f.blocks.insert(0x700000,5u32.to_le_bytes().to_vec());
    f.blocks.insert(0x710000,2u32.to_le_bytes().to_vec());
    assert_eq!(rows(&f,BASE,MANAGER,&[7,8]).unwrap(),expected);
    assert_eq!(u32at(&ids(&f,BASE,MANAGER).unwrap(),19),2);
}
#[test]fn changing_header_bitmap_or_entry_is_rejected(){
    struct Changing{f:Fixture,target:u64,len:usize,reads:Cell<usize>}
    impl Memory for Changing{fn read(&self,p:u64,n:usize)->Result<Vec<u8>>{
        let mut b=self.f.read(p,n)?;if p==self.target && n==self.len {
            self.reads.set(self.reads.get()+1);if self.reads.get()>1 {b[0]^=1;}
        }Ok(b)
    }}
    for (target,len) in [(MANAGER+1360,76),(DATA,24)] {
        assert!(rows(&Changing{f:fixture(),target,len,reads:Cell::new(0)},BASE,MANAGER,&[7]).is_err());
    }
    let mut f=fixture();put64(f.blocks.get_mut(&MANAGER).unwrap(),1392,0x700000);
    f.blocks.insert(0x700000,5u32.to_le_bytes().to_vec());
    assert!(rows(&Changing{f,target:0x700000,len:4,reads:Cell::new(0)},BASE,MANAGER,&[7]).is_err());
}
#[test]fn rpm_can_read_only_current_process_buffer_and_rejects_absent_memory(){
    let b=b"building-reader-test";assert_eq!(Process.read(b.as_ptr() as u64,b.len()).unwrap(),b);
    assert!(Process.read(1,8).is_err());assert!(Process.read(0x10000,8).is_err());
}
