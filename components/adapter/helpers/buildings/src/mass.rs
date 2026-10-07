//! Exact observed Mass storage/body lookup, bounded reads only; no UE calls.
use super::*;
use std::cell::RefCell;
include!("mass_signatures.rs");

struct Snapshot<'a,M:Memory>{memory:&'a M,reads:RefCell<BTreeMap<(u64,usize),Vec<u8>>>}
impl<M:Memory> Memory for Snapshot<'_,M>{
    fn read(&self,p:u64,n:usize)->Result<Vec<u8>>{
        require(p>=0x10000 && p.checked_add(n as u64).is_some_and(|x|x<0x800000000000) && n<=65536,"Invalid Mass read range")?;
        if let Some(value)=self.reads.borrow().get(&(p,n)){return Ok(value.clone())}
        require(self.reads.borrow().len()<32768,"Mass snapshot exceeds read budget")?;
        let value=self.memory.read(p,n)?;require(value.len()==n,"Incomplete Mass read")?;
        self.reads.borrow_mut().insert((p,n),value.clone());Ok(value)
    }
}
impl<M:Memory> Snapshot<'_,M>{fn verify(&self)->Result<()>{
    for ((p,n),value) in self.reads.borrow().iter(){require(self.memory.read(*p,*n)?==*value,"Mass state changed during capture")?}Ok(())
}}
fn r64(m:&impl Memory,p:u64)->Result<u64>{Ok(u64at(&m.read(p,8)?,0))}
fn r32(m:&impl Memory,p:u64)->Result<i32>{Ok(i32at(&m.read(p,4)?,0))}
fn pointer_hash(key:u64)->u32{
    let mut v=(key>>4) as u32;v=(v^(v>>16)).wrapping_mul(0x85ebca6b);
    v=(v^(v>>13)).wrapping_mul(0xc2b2ae35);v^(v>>16)
}
fn lookup(m:&impl Memory,address:u64,key:u64,pointer_key:bool)->Result<Option<i32>>{
    let h=m.read(address,76)?;let data=u64at(&h,0);let n=i32at(&h,8);let max=i32at(&h,12);
    let free=i32at(&h,52);let bit_num=i32at(&h,40);let bit_max=i32at(&h,44);
    require(free>=0 && n>=free && max>=n && max<=1_000_000 && bit_num==n && bit_max>=n,"Invalid archetype sparse bounds")?;
    if n==free{return Ok(None)}ptr(data)?;
    let buckets=i32at(&h,72);let heap=u64at(&h,64);let bits=u64at(&h,32);
    require(buckets>0 && buckets<=2_097_152 && (buckets as u32).is_power_of_two(),"Invalid archetype hash size")?;
    require(heap!=0 || buckets==1,"Invalid inline archetype hash")?;
    require(bits!=0 || bit_max<=128,"Invalid inline archetype bitmap")?;
    let hash=if pointer_key{pointer_hash(key)}else{key as u32};let bucket=hash&(buckets as u32-1);
    let hash_data=if heap!=0{ptr(heap)?}else{address+56};
    let mut slot=r32(m,hash_data+bucket as u64*4)?;let mut seen=BTreeSet::new();let stride=if pointer_key{24}else{16};
    while slot!=-1{
        require(slot>=0 && slot<n && seen.insert(slot) && seen.len()<=1024,"Invalid archetype hash chain")?;
        let bit=if bits!=0{m.read(ptr(bits)?+slot as u64/8,1)?[0]}else{h[16+slot as usize/8]};
        require(bit&(1<<(slot%8))!=0,"Archetype hash refers to free slot")?;
        let row=m.read(data+slot as u64*stride as u64,stride)?;
        require(i32at(&row,stride-4)==bucket as i32,"Archetype hash bucket mismatch")?;
        if (if pointer_key{u64at(&row,0)}else{u32at(&row,0) as u64})==key{return Ok(Some(i32at(&row,if pointer_key{8}else{4})))}
        slot=i32at(&row,stride-8);
    }Ok(None)
}
fn weak(m:&impl Memory,base:u64,raw:&[u8],pending:bool)->Result<u64>{
    let index=i32at(raw,0);let serial=i32at(raw,4);if serial==0 || index<0{return Ok(0)}
    let count=r32(m,base+0xCDBAE14)?;require((0..=16_000_000).contains(&count),"Invalid UObject inventory count")?;
    if index>=count{return Ok(0)}
    let chunks=ptr(r64(m,base+0xCDBAE00)?)?;let chunk=ptr(r64(m,chunks+(index as u64>>16)*8)?)?;
    let row=m.read(chunk+(index as u64&0xffff)*24,24)?;
    if i32at(&row,16)!=serial || u32at(&row,8)&if pending{0x10000000}else{0x10200000}!=0{return Ok(0)}
    ptr(u64at(&row,0))
}
struct Context{base:u64,manager:u64,storage:u64,capacity:u32,chunks:u64,chunk_count:u32,tags:u64,physics:u64,building_tag:u64}
impl Context{
    fn open(m:&impl Memory,base:u64,manager:u64,subsystem:u64)->Result<Self>{
        ptr(subsystem)?;let vt=r64(m,subsystem)?;
        require(vt==base+180098216,"Unsupported JgxMassSubsystem vtable")?;
        for (rva,bytes) in MASS_SIGNATURES {require(m.read(base+*rva,bytes.len())?==*bytes,"Mass/body code signature mismatch")?;}
        let mass=ptr(r64(m,subsystem+0x88)?)?;let sh=m.read(mass+0x20,65)?;
        require(sh[64]==2 && u64at(&sh,0)==base+0xA1E7908,"Unsupported Mass storage implementation")?;
        for (off,rva) in [(0x20,0x3A9DBA0),(0x40,0x3A9F520),(0x48,0x3AA5730)]{
            require(r64(m,base+0xA1E7908+off)?==base+rva,"Unsupported Mass storage accessor")?;
        }
        let capacity=u32at(&sh,16);let chunk_count=u32at(&sh,20);let chunks=ptr(u64at(&sh,32))?;
        require(capacity.is_power_of_two() && capacity<=1_048_576 && chunk_count>0 && chunk_count<=4096,"Invalid Mass storage bounds")?;
        let tags=ptr(r64(m,base+0xCFD08F0)?)?;let physics=ptr(r64(m,base+0xCFD0A18)?)?;
        for (address,size) in [(tags,16),(physics,48)]{
            let b=m.read(address+0x58,6)?;require(i32at(&b,0)==size && i16::from_le_bytes(b[4..6].try_into().unwrap())==8,"Unsupported Mass fragment layout")?;
        }
        Ok(Self{base,manager,storage:mass+0x20,capacity,chunks,chunk_count,tags,physics,building_tag:r64(m,base+0xD0126A8)?})
    }
    fn fragment(&self,m:&impl Memory,archetype:u64,data:u64,row:i32,ty:u64,size:u64)->Result<Option<u64>>{
        let Some(slot)=lookup(m,archetype+0x220,ty,true)?else{return Ok(None)};
        let count=r32(m,archetype+0x1B8)?;require(slot>=0 && slot<count && count<=1024,"Invalid fragment descriptor index")?;
        let heap=r64(m,archetype+0x1B0)?;require(heap!=0 || count<=16,"Invalid inline fragment descriptors")?;
        let begin=if heap!=0{ptr(heap)?}else{archetype+0xB0};let d=m.read(begin+slot as u64*16,16)?;
        let offset=i32at(&d,8);require(u64at(&d,0)==ty && (0..=16_777_216).contains(&offset),"Invalid fragment descriptor")?;
        let p=data.checked_add(offset as u64).and_then(|p|p.checked_add(row as u64*size)).ok_or("Fragment address overflow")?;ptr(p)?;Ok(Some(p))
    }
    fn entity(&self,m:&impl Memory,piece_id:u32,index:u32,handle:u64)->Result<Vec<u8>>{
        let id=handle as u32;let serial=(handle>>32) as u32;
        require(id>0 && id<i32::MAX as u32 && serial>0 && serial<=0x3fffffff && id/self.capacity<self.chunk_count,"Invalid Mass handle")?;
        let chunk=ptr(r64(m,self.chunks+(id/self.capacity) as u64*8)?)?;
        let storage_row=m.read(chunk+(id%self.capacity) as u64*24,24)?;
        require(u32at(&storage_row,16)&0x3fffffff==serial,"Mass entity serial changed")?;
        let archetype=ptr(u64at(&storage_row,0))?;let row=lookup(m,archetype+0x1D0,id as u64,false)?.ok_or("Entity absent from archetype")?;
        let chunk_capacity=r32(m,archetype+0x2A8)?;require(row>=0 && chunk_capacity>0 && chunk_capacity<=1_048_576,"Invalid archetype chunk capacity")?;
        let chunk_index=row/chunk_capacity;let row_in_chunk=row%chunk_capacity;
        let count=r32(m,archetype+0x1C8)?;require(chunk_index<count && count<=100000,"Invalid archetype chunk index")?;
        let chunks=ptr(r64(m,archetype+0x1C0)?)?;let data=ptr(r64(m,chunks+chunk_index as u64*0x90)?)?;
        let mut selected=false;
        if let Some(tags)=self.fragment(m,archetype,data,row_in_chunk,self.tags,16)?{
            let h=m.read(tags,16)?;let n=i32at(&h,8);let max=i32at(&h,12);require(n>=0 && max>=n && max<=128,"Invalid entity tags")?;
            if n>0 {let b=m.read(ptr(u64at(&h,0))?,n as usize*8)?;selected=(0..n as usize).any(|i|u64at(&b,i*8)==self.building_tag);}
        }
        let mut out=vec![0u8;96];out[..4].copy_from_slice(&index.to_le_bytes());out[4..12].copy_from_slice(&handle.to_le_bytes());out[13]=selected as u8;
        let Some(physics)=self.fragment(m,archetype,data,row_in_chunk,self.physics,48)?else{return Ok(out)};
        let f=m.read(physics,48)?;let body=ptr(u64at(&f,24))?+24;let b=m.read(body,0x148)?;
        require(f[40]<=5 && f[41..44].iter().all(|v|*v<=1) && f[43]==0,"Native building physics update is pending")?;
        let source=u64at(&f,8);let body_id=u32at(&f,16);
        require(source==self.manager && body_id==piece_id,"Native body owner/piece identity mismatch")?;
        let owner=weak(m,self.base,&b[0x140..0x148],false)?;let mut enabled=b[0x17];
        if owner!=0{
            let actor=r64(m,owner+0xA8)?;
            if actor!=0 && m.read(ptr(actor)?+0x65,1)?[0]&1==0{enabled=0}
            else{
                let target=ptr(r64(m,self.base+0xCF17150)?)?;let class=ptr(r64(m,owner+0x10)?)?;
                let a=r32(m,target+0x38)?;let n=r32(m,class+0x38)?;
                require((0..=1024).contains(&a) && (0..=1024).contains(&n),"Invalid body owner class ancestry")?;
                if a<=n && r64(m,ptr(r64(m,class+0x30)?)?+a as u64*8)?==target+0x30{enabled=m.read(owner+0x39F,1)?[0];}
            }
        }
        let external=weak(m,self.base,&b[0xA0..0xA8],true)?;
        let profile=if external!=0{r64(m,external+0x1AC)?}else{u64at(&b,0x44)};
        require(enabled<=5 && b[0x15]<=31 && b[0x68..0x88].iter().all(|v|*v<=2),"Invalid native body filter")?;
        require(enabled==0 || f[42]==1,"Native body is not initialized")?;
        let setup=u64at(&f,32);if enabled!=0{ptr(setup)?;}
        out[12]=1;out[14..20].copy_from_slice(&[f[41],f[42],f[43],f[40],enabled,b[0x15]]);
        out[20..52].copy_from_slice(&b[0x68..0x88]);out[52..60].copy_from_slice(&profile.to_le_bytes());
        out[60..68].copy_from_slice(&setup.to_le_bytes());out[68..76].copy_from_slice(&source.to_le_bytes());
        out[76..80].copy_from_slice(&body_id.to_le_bytes());out[80..88].copy_from_slice(&owner.to_le_bytes());out[88..96].copy_from_slice(&external.to_le_bytes());Ok(out)
    }
}
pub(super) fn request(memory:&impl Memory,base:u64,input:&[u8])->Result<Vec<u8>>{
    require(input.len()>=25 && &input[..5]==b"DWBQ2","Invalid Mass building request")?;
    let manager=u64at(input,5);let subsystem=u64at(input,13);let n=u32at(input,21) as usize;
    require(n>0 && n<=4 && input.len()==25+n*4,"Invalid Mass building request count")?;
    let ids:Vec<_>=(0..n).map(|i|u32at(input,25+i*4)).collect();
    require(ids.iter().copied().collect::<BTreeSet<_>>().len()==n,"Duplicate requested building")?;
    let m=Snapshot{memory,reads:RefCell::new(BTreeMap::new())};let map=Map::open(&m,manager,base)?;
    let context=Context::open(&m,base,manager,subsystem)?;
    let mut out=b"DWBBDY2".to_vec();out.extend(1u32.to_le_bytes());out.extend(manager.to_le_bytes());out.extend(subsystem.to_le_bytes());out.extend((n as u32).to_le_bytes());
    for id in ids{
        let (address,_,_)=map.lookup(&m,id)?;let data=m.read(address,688)?;let encoded=piece(&data,base,manager,id)?;
        let count=i32at(&data,640) as usize;require(count<=64,"Building has too many entities for bounded capture")?;
        let handles=if count>0{m.read(ptr(u64at(&data,632))?,count*8)?}else{vec![]};out.extend(encoded);
        let mut unique=BTreeSet::new();for i in 0..count{
            let handle=u64at(&handles,i*8);require(unique.insert(handle),"Duplicate entity handle in building")?;
            out.extend(context.entity(&m,id,i as u32,handle)?);
        }
    }
    let _=context.storage;map.stable(&m)?;m.verify()?;Ok(out)
}

#[cfg(test)] mod tests;
