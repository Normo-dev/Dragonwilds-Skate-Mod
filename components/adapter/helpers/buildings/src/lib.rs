//! Version-pinned, read-only lightweight building snapshot. No engine calls.
use std::{collections::{BTreeMap, BTreeSet}, ffi::c_void};
include!("loader_constants.rs");
include!("game_constants.rs");
mod mass;
type Result<T> = std::result::Result<T, String>;
type Lua = *mut c_void;
type Push = unsafe extern "C" fn(Lua, *const u8, usize) -> *const u8;
type ToString = unsafe extern "C" fn(Lua, i32, *mut usize) -> *const u8;
#[link(name="kernel32")]
unsafe extern "system" {
    fn GetCurrentProcess() -> *mut c_void;
    fn ReadProcessMemory(process:*mut c_void, address:*const c_void, buffer:*mut c_void, len:usize, read:*mut usize)->i32;
    fn GetModuleHandleW(name:*const u16)->*mut c_void;
}
trait Memory { fn read(&self, address:u64, len:usize)->Result<Vec<u8>>; }
struct Process;
impl Memory for Process {
    fn read(&self, address:u64, len:usize)->Result<Vec<u8>> {
        if address<0x10000 || address.checked_add(len as u64).is_none_or(|p|p>0x0000_8000_0000_0000) || len>4*1024*1024 {
            return Err("Invalid bounded memory range".into());
        }
        let mut data=vec![0;len]; let mut read=0;
        let ok=unsafe{ReadProcessMemory(GetCurrentProcess(),address as *const c_void,data.as_mut_ptr().cast(),len,&mut read)};
        if ok==0 || read!=len {return Err("Building memory was unavailable".into())} Ok(data)
    }
}
fn u32at(b:&[u8],i:usize)->u32 {u32::from_le_bytes(b[i..i+4].try_into().unwrap())}
fn i32at(b:&[u8],i:usize)->i32 {i32::from_le_bytes(b[i..i+4].try_into().unwrap())}
fn u64at(b:&[u8],i:usize)->u64 {u64::from_le_bytes(b[i..i+8].try_into().unwrap())}
fn ptr(p:u64)->Result<u64> {if p<0x10000 || p>=0x0000_8000_0000_0000 || p%8!=0 {Err("Invalid object pointer".into())}else{Ok(p)}}
fn require(ok:bool, text:&str)->Result<()> {if ok {Ok(())}else{Err(text.into())}}
fn pe(memory:&impl Memory,base:u64,timestamp:u32,size:u32)->Result<()> {
    let dos=memory.read(base,64)?;
    require(&dos[..2]==b"MZ","Invalid module DOS header")?;
    let offset=u32at(&dos,60) as u64;
    require(offset<=4096,"Invalid module PE offset")?;
    let nt=memory.read(base+offset,84)?;
    require(&nt[..4]==b"PE\0\0" && u32at(&nt,8)==timestamp && u32at(&nt,80)==size && nt[4..6]==[0x64,0x86],"Unsupported module version")
}
fn game(memory:&impl Memory,base:u64)->Result<()> {
    pe(memory,base,GAME_TIMESTAMP,GAME_SIZE)?;
    for (rva,signature) in GAME_SIGNATURES {require(memory.read(base+*rva,signature.len())?==*signature,"Game code signature mismatch")?;} Ok(())
}
struct Map {address:u64,header:Vec<u8>,data:u64,num:usize,live:usize,bits:Vec<u8>,hash:u64,hash_size:usize}
impl Map {
    fn open(m:&impl Memory,address:u64,base:u64)->Result<Self> {
        ptr(address)?;
        require(u64at(&m.read(address,8)?,0)==base+MANAGER_VTABLE,"Wrong building manager vtable")?;
        require(u64at(&m.read(address+1328,8)?,0)==base+RESOLVER_VTABLE,"Wrong building resolver vtable")?;
        let h=m.read(address+1360,76)?;
        let num=i32at(&h,8); let max=i32at(&h,12); let free=i32at(&h,52);
        let bits_num=i32at(&h,40); let bits_max=i32at(&h,44);
        require(free>=0 && num>=free && max>=num && max<=100000 && bits_num==num && bits_max>=num,"Invalid native sparse map bounds")?;
        let live=(num-free) as usize;
        require(live<=50000,"Building inventory exceeds capture bound")?;
        let data=u64at(&h,0);if num>0 {ptr(data)?;}
        let bitheap=u64at(&h,32);let bytes=((num as usize+31)/32)*4;
        let bits=if bitheap==0 {require(bits_max<=128,"Invalid inline allocation bitmap")?;h[16..16+bytes].to_vec()}
            else {ptr(bitheap)?;m.read(bitheap,bytes)?};
        require((0..num as usize).filter(|i|bits[i/8]&(1<<(i%8))!=0).count()==live,"Native allocation bitmap changed")?;
        let hash_size=i32at(&h,72);let hash_heap=u64at(&h,64);
        require(hash_size>=0 && hash_size<=262144 && (live==0 || (hash_size>0 && (hash_size as u32).is_power_of_two())),"Invalid native hash table size")?;
        let hash=if hash_heap==0 {require(hash_size<=1,"Invalid inline hash table")?;address+1416}else{ptr(hash_heap)?};
        Ok(Self{address,header:h,data,num:num as usize,live,bits,hash,hash_size:hash_size as usize})
    }
    fn allocated(&self,i:usize)->bool {i<self.num && self.bits[i/8]&(1<<(i%8))!=0}
    fn stable(&self,m:&impl Memory)->Result<()> {
        require(m.read(self.address+1360,76)?==self.header,"Building map changed during capture")?;
        let bitheap=u64at(&self.header,32);
        if bitheap!=0 {require(m.read(bitheap,self.bits.len())?==self.bits,"Building allocation changed during capture")?;} Ok(())
    }
    fn entries(&self,m:&impl Memory)->Result<(BTreeMap<u32,u64>,Vec<u8>)> {
        let data=if self.num==0 {vec![]}else{m.read(self.data,self.num*24)?};
        let mut result=BTreeMap::new();let mut pointers=BTreeSet::new();
        for i in 0..self.num {if self.allocated(i) {
            let row=&data[i*24..(i+1)*24]; let id=u32at(row,0);let p=ptr(u64at(row,8))?;
            require(result.insert(id,p).is_none() && pointers.insert(p),"Duplicate native piece identity")?;
        }} require(result.len()==self.live,"Building inventory changed")?; Ok((result,data))
    }
    fn lookup(&self,m:&impl Memory,id:u32)->Result<(u64,Vec<u8>,u64)> {
        require(self.live>0 && self.hash_size>0,"Requested building is absent")?;
        let bucket=(id as usize)&(self.hash_size-1);
        let mut slot=i32at(&m.read(self.hash+bucket as u64*4,4)?,0); let mut seen=BTreeSet::new();
        while slot!=-1 {
            require(slot>=0 && self.allocated(slot as usize) && seen.insert(slot) && seen.len()<=1024,"Invalid native building hash chain")?;
            let addr=self.data+slot as u64*24;let row=m.read(addr,24)?;
            require(i32at(&row,20)==bucket as i32,"Building hash bucket mismatch")?;
            if u32at(&row,0)==id {return Ok((ptr(u64at(&row,8))?,row,addr));}
            slot=i32at(&row,16);
        } Err("Requested building is absent".into())
    }
}
fn header(magic:&[u8;7],manager:u64,count:usize)->Vec<u8> {
    let mut out=magic.to_vec();out.extend(1u32.to_le_bytes());out.extend(manager.to_le_bytes());out.extend((count as u32).to_le_bytes());out
}
fn ids(memory:&impl Memory,base:u64,manager:u64)->Result<Vec<u8>> {
    let map=Map::open(memory,manager,base)?;let (entries,before)=map.entries(memory)?;
    map.stable(memory)?;
    if map.num>0 {require(memory.read(map.data,map.num*24)?==before,"Building IDs changed during capture")?;}
    let mut out=header(b"DWBIDS1",manager,entries.len());for id in entries.keys(){out.extend(id.to_le_bytes())}Ok(out)
}
fn piece(bytes:&[u8],base:u64,manager:u64,id:u32)->Result<Vec<u8>> {
    require(u64at(bytes,0)==base+PIECE_VTABLE && u64at(bytes,464)==manager && u32at(bytes,668)==id,"Native building identity mismatch")?;
    let asset=ptr(u64at(bytes,368))?;let derived=ptr(u64at(bytes,376))?;
    let count=i32at(bytes,640);let capacity=i32at(bytes,644);
    require(count>=0 && capacity>=count && capacity<=1024,"Invalid native building entity array")?;
    if count>0 {ptr(u64at(bytes,632))?;}
    require(bytes[672..676].iter().all(|v|*v<=1),"Invalid native building flag")?;
    let mut transform=Vec::new();let mut values=Vec::new();
    for (start,count) in [(480,4),(512,3),(544,3)] {for i in 0..count {
        let value=f64::from_le_bytes(bytes[start+i*8..start+(i+1)*8].try_into().unwrap());
        require(value.is_finite(),"Nonfinite native transform")?;values.push(value);transform.extend(value.to_le_bytes());
    }}
    require((values[..4].iter().map(|v|v*v).sum::<f64>()-1.).abs()<0.0001,"Invalid native rotation")?;
    let mut out=id.to_le_bytes().to_vec();out.extend(asset.to_le_bytes());out.extend(derived.to_le_bytes());out.extend(transform);out.extend(&bytes[672..676]);out.extend((count as u32).to_le_bytes());Ok(out)
}
fn rows(memory:&impl Memory,base:u64,manager:u64,requested:&[u32])->Result<Vec<u8>> {
    require(!requested.is_empty() && requested.len()<=16 && requested.iter().copied().collect::<BTreeSet<_>>().len()==requested.len(),"Invalid building row request")?;
    let map=Map::open(memory,manager,base)?;let mut out=header(b"DWBROW1",manager,requested.len());let mut saved=Vec::new();
    for id in requested {
        let (address,entry,entry_addr)=map.lookup(memory,*id)?;let data=memory.read(address,688)?;
        let encoded=piece(&data,base,manager,*id)?;out.extend(&encoded);saved.push((*id,address,encoded,entry_addr,entry));
    }
    for (id,address,before,entry_addr,entry) in saved {
        require(piece(&memory.read(address,688)?,base,manager,id)?==before && memory.read(entry_addr,24)?==entry,"Native building changed during capture")?;
    }
    map.stable(memory)?;Ok(out)
}
fn request(memory:&impl Memory,base:u64,input:&[u8],want_rows:bool)->Result<Vec<u8>> {
    require(input.len()>=13 && &input[..5]==b"DWBQ1","Invalid building request header")?;
    let manager=u64at(input,5);
    if !want_rows {require(input.len()==13,"Invalid ID request size")?;return ids(memory,base,manager)}
    require(input.len()>=17,"Missing building row count")?;let count=u32at(input,13) as usize;
    require(count>0 && count<=16 && input.len()==17+count*4,"Invalid row request size")?;
    let requested:Vec<_>=(0..count).map(|i|u32at(input,17+i*4)).collect();rows(memory,base,manager,&requested)
}
fn apis(memory:&impl Memory)->Result<(Push,ToString)> {
    let name:Vec<_>="UE4SS.dll\0".encode_utf16().collect();let base=unsafe{GetModuleHandleW(name.as_ptr())} as u64;
    require(base!=0,"UE4SS is absent")?;pe(memory,base,LOADER_TIMESTAMP,LOADER_IMAGE_SIZE)?;
    require(memory.read(base+PUSH_RVA as u64,PUSH_PREFIX.len())?==PUSH_PREFIX && memory.read(base+TOSTRING_RVA as u64,TOSTRING_PREFIX.len())?==TOSTRING_PREFIX,"Unsupported Lua ABI")?;
    Ok(unsafe{(std::mem::transmute::<usize,Push>(base as usize+PUSH_RVA),std::mem::transmute::<usize,ToString>(base as usize+TOSTRING_RVA))})
}
fn lua_call(state:Lua,mode:u8)->i32 {
    let memory=Process;let Ok((push,to_string))=apis(&memory)else{return 0};
    let result=std::panic::catch_unwind(||->Result<Vec<u8>>{
        let mut len=0;let data=unsafe{to_string(state,1,&mut len)};
        require(!data.is_null() && len<=81,"Invalid Lua building request")?;
        let input=memory.read(data as u64,len)?;
        let base=unsafe{GetModuleHandleW(std::ptr::null())} as u64;game(&memory,base)?;
        if mode==2{mass::request(&memory,base,&input)}else{request(&memory,base,&input,mode==1)}
    });
    let output=match result {Ok(Ok(v))=>v,Ok(Err(e))=>[b"DWBERR1".as_slice(),e.as_bytes()].concat(),Err(_)=>b"DWBERR1Building reader rejected invalid data".to_vec()};
    unsafe{push(state,output.as_ptr(),output.len());}1
}
#[unsafe(no_mangle)]pub extern "C" fn skate_building_ids(state:Lua)->i32 {lua_call(state,0)}
#[unsafe(no_mangle)]pub extern "C" fn skate_building_read(state:Lua)->i32 {lua_call(state,1)}
#[unsafe(no_mangle)]pub extern "C" fn skate_building_read_bodies(state:Lua)->i32 {lua_call(state,2)}

#[cfg(test)] mod tests;
