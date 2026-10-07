//! Windows shared-memory transport. No game objects or skating physics here.
use std::{ffi::c_void, sync::atomic::{AtomicBool,AtomicI64, AtomicUsize, Ordering}};
include!("loader_constants.rs");
mod launcher;

type Lua = *mut c_void;
type PushString = unsafe extern "C" fn(Lua,*const u8,usize)->*const u8;
type ToString = unsafe extern "C" fn(Lua,i32,*mut usize)->*const u8;
#[link(name="kernel32")]
unsafe extern "system" {
    fn OpenFileMappingW(access:u32,inherit:i32,name:*const u16)->*mut c_void;
    fn MapViewOfFile(handle:*mut c_void,access:u32,high:u32,low:u32,size:usize)->*mut c_void;
    fn UnmapViewOfFile(view:*const c_void)->i32;
    fn CloseHandle(handle:*mut c_void)->i32;
    fn GetModuleHandleW(name:*const u16)->*mut c_void;
}
const CAP:[usize;4]=[65536,8192,8192,4*1024*1024];
static VIEWS:[AtomicUsize;4]=[AtomicUsize::new(0),AtomicUsize::new(0),AtomicUsize::new(0),AtomicUsize::new(0)];
static HANDLES:[AtomicUsize;4]=[AtomicUsize::new(0),AtomicUsize::new(0),AtomicUsize::new(0),AtomicUsize::new(0)];
static LAST_FRAME:AtomicI64=AtomicI64::new(0);
static TEST_NAMESPACE:AtomicBool=AtomicBool::new(false);
fn view(channel:usize)->usize {
    let cached=VIEWS[channel].load(Ordering::Acquire);
    if cached!=0 { return cached; }
    let suffix=["Frame","Host","Status","Collision"][channel];
    let prefix=if TEST_NAMESPACE.load(Ordering::Relaxed) {"Local\\DragonwildsSkate.test.v3."}else{"Local\\DragonwildsSkate.v3."};
    let name:Vec<u16>=format!("{prefix}{suffix}\0").encode_utf16().collect();
    let access=6; // The Python publisher and Lua consumer use the same helper.
    unsafe {
        let handle=OpenFileMappingW(access,0,name.as_ptr());
        if handle.is_null() { return 0; }
        let ptr=MapViewOfFile(handle,access,0,0,CAP[channel]);
        if ptr.is_null() { CloseHandle(handle);return 0; }
        HANDLES[channel].store(handle as usize,Ordering::Release);
        VIEWS[channel].store(ptr as usize,Ordering::Release);
        ptr as usize
    }
}
fn apis()->Option<(PushString,ToString)> {
    let name:Vec<u16>="UE4SS.dll\0".encode_utf16().collect();
    unsafe {
        let base=GetModuleHandleW(name.as_ptr()) as *const u8;
        if base.is_null() { return None; }
        let pe=(base.add(60) as *const u32).read_unaligned() as usize;
        if pe>4096 || (base.add(pe) as *const u32).read_unaligned()!=0x4550 { return None; }
        if (base.add(pe+8) as *const u32).read_unaligned()!=LOADER_TIMESTAMP ||
           (base.add(pe+24+56) as *const u32).read_unaligned()!=LOADER_IMAGE_SIZE { return None; }
        if std::slice::from_raw_parts(base.add(PUSH_RVA),PUSH_PREFIX.len())!=PUSH_PREFIX ||
           std::slice::from_raw_parts(base.add(TOSTRING_RVA),TOSTRING_PREFIX.len())!=TOSTRING_PREFIX { return None; }
        Some((std::mem::transmute(base.add(PUSH_RVA)),std::mem::transmute(base.add(TOSTRING_RVA))))
    }
}
fn copy(channel:usize,buffer:&mut [u8])->Option<(usize,i64)> {
    let p=view(channel) as *const u8;
    if p.is_null() { return None; }
    unsafe {
        let seq=&*(p.add(8) as *const AtomicI64);
        for _ in 0..2 {
            let before=seq.load(Ordering::Acquire);
            if before<=0 || before&1!=0 { continue; }
            let len=(p.add(16) as *const u32).read_unaligned() as usize;
            if len>CAP[channel]-64 || len>buffer.len() { return None; }
            std::ptr::copy_nonoverlapping(p.add(64),buffer.as_mut_ptr(),len);
            // Acquire fence keeps the payload copy before the second sequence
            // check. Odd/changing packets are skipped, never waited on.
            std::sync::atomic::fence(Ordering::Acquire);
            if seq.load(Ordering::Acquire)==before { return Some((len,before)); }
        }
    }
    None
}
fn read_lua(state:Lua,channel:usize,only_new:bool)->i32 {
    let Some((push,_))=apis() else { return 0; };
    if only_new {
        let p=view(channel) as *const u8;
        if p.is_null() { return 0; }
        let current=unsafe{(&*(p.add(8) as *const AtomicI64)).load(Ordering::Acquire)};
        if current==LAST_FRAME.load(Ordering::Relaxed) { return 0; }
    }
    let mut bytes=[0u8;65536];
    let Some((len,seq))=copy(channel,&mut bytes) else { return 0; };
    unsafe { push(state,bytes.as_ptr(),len); }
    if only_new { LAST_FRAME.store(seq,Ordering::Relaxed); }
    1
}
#[unsafe(no_mangle)] pub extern "C" fn skate_check(state:Lua)->i32 {
    let Some((push,_))=apis() else { return 0; };
    unsafe { push(state,b"shared-memory-v3".as_ptr(),16); } 1
}
#[unsafe(no_mangle)] pub extern "C" fn skate_read_frame(state:Lua)->i32 { read_lua(state,0,true) }
#[unsafe(no_mangle)] pub extern "C" fn skate_read_status(state:Lua)->i32 { read_lua(state,2,false) }
// Monotonic wall time for frame budgets. Lua os.clock measures the CPU time of
// the entire game process, including other engine threads.
#[unsafe(no_mangle)] pub extern "C" fn skate_clock(state:Lua)->i32 {
    static START:std::sync::OnceLock<std::time::Instant>=std::sync::OnceLock::new();
    let Some((push,_))=apis() else {return 0};
    let seconds=START.get_or_init(std::time::Instant::now).elapsed().as_secs_f64();
    unsafe {push(state,seconds.to_le_bytes().as_ptr(),8);}
    1
}
fn write_lua(state:Lua,channel:u32)->i32 {
    let Some((push,to_string))=apis() else { return 0; };
    let mut len=0;
    unsafe {
        let data=to_string(state,1,&mut len);
        if data.is_null() || len>CAP[channel as usize]-64 { return 0; }
        if ipc_publish(channel,data,len)!=1 { return 0; }
        push(state,b"ok".as_ptr(),2);
    }
    1
}
#[unsafe(no_mangle)] pub extern "C" fn skate_write_host(state:Lua)->i32 {write_lua(state,1)}
#[unsafe(no_mangle)] pub extern "C" fn skate_write_collision(state:Lua)->i32 {write_lua(state,3)}
// Optional public-package bootstrap. Arguments are literal paths, never shell
// command text. The supervisor owns its descendants and observes this game PID.
#[unsafe(no_mangle)] pub extern "C" fn skate_start_relay(state:Lua)->i32 {
    let Some((push,to_string))=apis() else {return 0};
    let value=(|| -> Result<String,String> {
        let mut paths=Vec::new();
        for index in 1..=2 {
            let mut len=0;
            let ptr=unsafe{to_string(state,index,&mut len)};
            if ptr.is_null() || len==0 || len>32767 {return Err("Invalid launcher path".into());}
            let bytes=unsafe{std::slice::from_raw_parts(ptr,len)};
            let text=std::str::from_utf8(bytes).map_err(|_|"Launcher path is not UTF-8")?;
            if text.contains('\0') {return Err("Invalid launcher path".into());}
            paths.push(text.to_owned());
        }
        let child=launcher::start(&paths[0],&paths[1]).map_err(|e|e.to_string())?;
        Ok(format!("started:{}",child.id()))
    })();
    let text=value.unwrap_or_else(|error|format!("error:{error}"));
    unsafe{push(state,text.as_ptr(),text.len());}1
}
#[unsafe(no_mangle)] pub unsafe extern "C" fn ipc_publish(channel:u32,data:*const u8,len:usize)->i32 {
    if channel>3 || data.is_null() || len>CAP[channel as usize]-64 { return 0; }
    let p=view(channel as usize) as *mut u8;
    if p.is_null() { return 0; }
    unsafe {
        let seq=&*(p.add(8) as *const AtomicI64);
        let mut before=seq.load(Ordering::Acquire);
        if before&1!=0 { before+=1; }
        seq.store(before+1,Ordering::SeqCst);
        std::ptr::copy_nonoverlapping(data,p.add(64),len);
        (p.add(16) as *mut u32).write_unaligned(len as u32);
        seq.store(before+2,Ordering::Release);
    }
    1
}
// Plain C diagnostic entrypoint for testing the transport outside the game.
#[unsafe(no_mangle)] pub extern "C" fn ipc_use_test_namespace()->i32 {
    if VIEWS.iter().any(|v|v.load(Ordering::Acquire)!=0) {return 0;}
    TEST_NAMESPACE.store(true,Ordering::Release);1
}
#[unsafe(no_mangle)] pub unsafe extern "C" fn ipc_copy(channel:u32,dest:*mut u8,capacity:usize)->i32 {
    if channel>3 || dest.is_null() || capacity>4*1024*1024 { return -1; }
    let buffer=unsafe { std::slice::from_raw_parts_mut(dest,capacity) };
    copy(channel as usize,buffer).map(|(len,_)|len as i32).unwrap_or(-1)
}
#[unsafe(no_mangle)] pub extern "system" fn DllMain(_:usize,reason:u32,_:usize)->i32 {
    if reason==0 {
        for i in 0..4 {
            let ptr=VIEWS[i].swap(0,Ordering::AcqRel);
            let handle=HANDLES[i].swap(0,Ordering::AcqRel);
            unsafe {
                if ptr!=0 { UnmapViewOfFile(ptr as *const c_void); }
                if handle!=0 { CloseHandle(handle as *mut c_void); }
            }
        }
    }
    1
}
