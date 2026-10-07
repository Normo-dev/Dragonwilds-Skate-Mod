use super::*;
use std::cell::Cell;
const BASE:u64=0x100000;const MANAGER:u64=0x200000;const SUB:u64=0x300000;const PIECE:u64=0x400000;
const ARCH:u64=0x800000;const PHYS:u64=0xA00000;const BODY:u64=0xC00018;
#[derive(Clone)]struct Fixture{blocks:BTreeMap<u64,Vec<u8>>}
impl Memory for Fixture{fn read(&self,p:u64,n:usize)->Result<Vec<u8>>{
    for (start,value) in &self.blocks{if p>=*start && p+n as u64<=*start+value.len() as u64{return Ok(value[(p-start) as usize..(p-start) as usize+n].to_vec())}}
    Err(format!("unmapped {p:x}/{n}"))
}}
fn u32put(b:&mut[u8],i:usize,v:u32){b[i..i+4].copy_from_slice(&v.to_le_bytes())}
fn u64put(b:&mut[u8],i:usize,v:u64){b[i..i+8].copy_from_slice(&v.to_le_bytes())}
fn block(f:&mut Fixture,p:u64,n:usize)->&mut Vec<u8>{f.blocks.insert(p,vec![0;n]);f.blocks.get_mut(&p).unwrap()}
fn map(b:&mut[u8],off:usize,data:u64,n:u32){
    u64put(b,off,data);u32put(b,off+8,n);u32put(b,off+12,n);u32put(b,off+16,(1<<n)-1);
    u32put(b,off+40,n);u32put(b,off+44,128);u32put(b,off+56,0);u32put(b,off+72,1);
}
fn fixture()->Fixture{
    let mut f=Fixture{blocks:BTreeMap::new()};
    let b=block(&mut f,MANAGER,0x600);u64put(b,0,BASE+MANAGER_VTABLE);u64put(b,0x530,BASE+RESOLVER_VTABLE);map(b,0x550,0x500000,1);
    let b=block(&mut f,0x500000,24);u32put(b,0,7);u64put(b,8,PIECE);u32put(b,16,u32::MAX);
    let b=block(&mut f,PIECE,688);u64put(b,0,BASE+PIECE_VTABLE);u64put(b,368,0xD00000);u64put(b,376,0xD01000);u64put(b,464,MANAGER);
    for off in [504,544,552,560]{u64put(b,off,1f64.to_bits())}u32put(b,668,7);b[675]=1;
    u64put(b,632,0xD02000);u32put(b,640,1);u32put(b,644,1);u64put(block(&mut f,0xD02000,8),0,(22u64<<32)|33);
    let b=block(&mut f,SUB,0x90);u64put(b,0,BASE+180098216);u64put(b,0x88,0x600000);
    let b=block(&mut f,0x600020,65);u64put(b,0,BASE+0xA1E7908);u32put(b,16,65536);u32put(b,20,1);u64put(b,32,0x700000);b[64]=2;
    let b=block(&mut f,BASE+0xA1E7908,0x50);for(off,rva)in[(0x20,0x3A9DBA0),(0x40,0x3A9F520),(0x48,0x3AA5730)]{u64put(b,off,BASE+rva)}
    for(rva,bytes)in MASS_SIGNATURES{block(&mut f,BASE+*rva,bytes.len()).copy_from_slice(bytes)}
    u64put(block(&mut f,BASE+0xCFD08F0,8),0,0xB00000);u64put(block(&mut f,BASE+0xCFD0A18,8),0,0xB01000);
    for(ptr,size)in[(0xB00000,16),(0xB01000,48)]{let b=block(&mut f,ptr+0x58,6);u32put(b,0,size);b[4]=8;}
    u64put(block(&mut f,BASE+0xD0126A8,8),0,12345);
    u64put(block(&mut f,0x700000,8),0,0x710000);let b=block(&mut f,0x710000+33*24,24);u64put(b,0,ARCH);u32put(b,16,22);
    let b=block(&mut f,ARCH,0x300);map(b,0x1D0,0x900000,1);map(b,0x220,0x910000,2);
    u32put(b,0x2A8,128);u64put(b,0x1C0,0x920000);u32put(b,0x1C8,1);u32put(b,0x1B8,2);
    u64put(b,0xB0,0xB00000);u64put(b,0xC0,0xB01000);u32put(b,0xC8,128);
    let b=block(&mut f,0x900000,16);u32put(b,0,33);u32put(b,8,u32::MAX);
    let b=block(&mut f,0x910000,48);u64put(b,0,0xB00000);u32put(b,16,1);u64put(b,24,0xB01000);u32put(b,32,1);u32put(b,40,u32::MAX);
    u64put(block(&mut f,0x920000,0x90),0,PHYS);let b=block(&mut f,PHYS,256);
    u64put(b,0,0x930000);u32put(b,8,1);u32put(b,12,1);u64put(b,128+8,MANAGER);u32put(b,128+16,7);
    u64put(b,128+24,BODY-24);u64put(b,128+32,0xD03000);b[168..172].copy_from_slice(&[3,1,1,0]);
    u64put(block(&mut f,0x930000,8),0,12345);let b=block(&mut f,BODY,0x148);b[0x15]=27;b[0x17]=3;b[0x68..0x88].fill(2);u64put(b,0x44,54321);f
}
fn input()->Vec<u8>{let mut q=b"DWBQ2".to_vec();q.extend(MANAGER.to_le_bytes());q.extend(SUB.to_le_bytes());q.extend(1u32.to_le_bytes());q.extend(7u32.to_le_bytes());q}
#[test]fn exact_body_protocol_preserves_native_filters_and_identity(){
    let out=request(&fixture(),BASE,&input()).unwrap();assert_eq!(out.len(),31+108+96);assert_eq!(&out[..7],b"DWBBDY2");
    assert_eq!(u64at(&out,19),SUB);let e=&out[139..];assert_eq!(u32at(e,0),0);assert_eq!(u64at(e,4),(22u64<<32)|33);
    assert_eq!(&e[12..20],&[1,1,1,1,0,3,3,27]);assert_eq!(&e[20..52],&[2;32]);assert_eq!(u64at(e,52),54321);
    assert_eq!(u64at(e,60),0xD03000);assert_eq!(u64at(e,68),MANAGER);assert_eq!(u32at(e,76),7);
}
#[test]fn current_disabled_body_is_not_inferred_from_active_flags(){
    let mut f=fixture();let b=f.blocks.get_mut(&BODY).unwrap();b[0x17]=0;b[0x68+17]=0;
    f.blocks.get_mut(&PHYS).unwrap()[128+41]=0;
    let out=request(&f,BASE,&input()).unwrap();let e=&out[139..];assert_eq!(e[14],0);assert_eq!(e[18],0);assert_eq!(e[37],0);
}
#[test]fn unknown_dirty_stale_or_mismatched_state_fails_whole_request(){
    for kind in 0..8{
        let mut f=fixture();match kind{
            0=>f.blocks.get_mut(&PHYS).unwrap()[128+43]=1,
            1=>u32put(f.blocks.get_mut(&(0x710000+33*24)).unwrap(),16,23),
            2=>u64put(f.blocks.get_mut(&PHYS).unwrap(),128+8,MANAGER+8),
            3=>u32put(f.blocks.get_mut(&PHYS).unwrap(),128+16,8),
            4=>f.blocks.get_mut(&BODY).unwrap()[0x68]=3,
            5=>u32put(f.blocks.get_mut(&ARCH).unwrap(),0x220+16,1),
            6=>f.blocks.get_mut(&0x600020).unwrap()[64]=1,
            _=>f.blocks.get_mut(&PHYS).unwrap()[128+42]=0,
        }assert!(request(&f,BASE,&input()).is_err(),"case{kind}");
    }
}
#[test]fn absence_of_physics_fragment_is_explicit(){
    let mut f=fixture();u64put(f.blocks.get_mut(&0x910000).unwrap(),24,0xB02000);
    let out=request(&f,BASE,&input()).unwrap();assert_eq!(out[139+12],0);assert_eq!(out[139+13],1);assert_eq!(out[139+18],0);
}
#[test]fn snapshot_rejects_filter_change_and_request_bounds(){
    struct Changing{f:Fixture,count:Cell<u32>}
    impl Memory for Changing{fn read(&self,p:u64,n:usize)->Result<Vec<u8>>{let mut b=self.f.read(p,n)?;
        if p==BODY&&n==0x148{self.count.set(self.count.get()+1);if self.count.get()>1{b[0x68]=0}}Ok(b)}}
    assert!(request(&Changing{f:fixture(),count:Cell::new(0)},BASE,&input()).is_err());
    let mut q=input();u32put(&mut q,21,5);assert!(request(&fixture(),BASE,&q).is_err());
}
#[test]fn weak_owner_actor_disable_and_external_profile_are_resolved(){
    let mut f=fixture();let b=f.blocks.get_mut(&BODY).unwrap();u32put(b,0x140,1);u32put(b,0x144,22);u32put(b,0xA0,2);u32put(b,0xA4,33);
    u32put(block(&mut f,BASE+0xCDBAE14,4),0,3);u64put(block(&mut f,BASE+0xCDBAE00,8),0,0xE00000);u64put(block(&mut f,0xE00000,8),0,0xE10000);
    let b=block(&mut f,0xE10000,72);u64put(b,24,0xE20000);u32put(b,40,22);u64put(b,48,0xE30000);u32put(b,64,33);
    u64put(block(&mut f,0xE20000+0xA8,8),0,0xE40000);block(&mut f,0xE40000+0x65,1);
    u64put(block(&mut f,0xE30000+0x1AC,8),0,999);
    let out=request(&f,BASE,&input()).unwrap();assert_eq!(out[139+18],0);assert_eq!(u64at(&out,139+52),999);assert_eq!(u64at(&out,139+80),0xE20000);
}
#[test]fn native_owner_class_override_and_unrelated_class_match_source_branch(){
    let mut f=fixture();let b=f.blocks.get_mut(&BODY).unwrap();u32put(b,0x140,1);u32put(b,0x144,22);
    u32put(block(&mut f,BASE+0xCDBAE14,4),0,2);u64put(block(&mut f,BASE+0xCDBAE00,8),0,0xE00000);u64put(block(&mut f,0xE00000,8),0,0xE10000);
    let b=block(&mut f,0xE10000,48);u64put(b,24,0xE20000);u32put(b,40,22);
    let b=block(&mut f,0xE20000,0x3A0);u64put(b,0x10,0xE60000);b[0x39F]=1;
    u64put(block(&mut f,BASE+0xCF17150,8),0,0xE50000);u32put(block(&mut f,0xE50038,4),0,1);
    let b=block(&mut f,0xE60030,12);u64put(b,0,0xE70000);u32put(b,8,2);
    u64put(block(&mut f,0xE70008,8),0,0xE50030);
    let out=request(&f,BASE,&input()).unwrap();assert_eq!(out[139+18],1);
    u64put(f.blocks.get_mut(&0xE70008).unwrap(),0,0xE50038);
    let out=request(&f,BASE,&input()).unwrap();assert_eq!(out[139+18],3);
}
