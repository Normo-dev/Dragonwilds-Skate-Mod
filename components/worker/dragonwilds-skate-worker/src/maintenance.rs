//! Native cache-maintenance barrier. This module never reads or deletes caches.
//!
//! A job permit is acquired before any cache input/build operation and is moved
//! into its worker closure. Dropping a JoinHandle does not release the permit.
//! The protocol thread separately supplies whether an uninstalled result still
//! exists, closing the gap between a finished job and its accepted installation.
use std::sync::{Arc, Mutex};

#[derive(Clone)]
pub struct Maintenance {
    inner: Arc<Inner>,
}

struct Inner {
    worker_id: String,
    state: Mutex<State>,
}

#[derive(Default)]
struct State {
    active_jobs: usize,
    next_lease: u64,
    lease: Option<String>,
}

/// One permit, one drop. It deliberately cannot be cloned or manually cleared.
pub struct CacheJob {
    inner: Arc<Inner>,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Snapshot {
    pub worker_id: String,
    pub active_cache_jobs: usize,
    pub lease_active: bool,
    pub pending_result: bool,
    pub quiescent: bool,
    pub retirement_allowed: bool,
}

impl Maintenance {
    pub fn new() -> Result<Self, String> {
        let entropy = worker_entropy()?;
        let id = entropy.iter().map(|v| format!("{v:02x}")).collect();
        Ok(Self::with_identity(id))
    }

    fn with_identity(worker_id: String) -> Self {
        Self {
            inner: Arc::new(Inner {
                worker_id,
                state: Mutex::new(State::default()),
            }),
        }
    }

    pub fn worker_id(&self) -> &str {
        &self.inner.worker_id
    }

    fn lock(&self) -> Result<std::sync::MutexGuard<'_, State>, String> {
        self.inner
            .state
            .lock()
            .map_err(|_| "Native maintenance state is poisoned; restart the worker".into())
    }

    /// Covers synchronous input I/O too. Existing command-specific reset and
    /// pending-job policies stay at their call sites. A permitted reset cannot
    /// erase an older job's permit, even when its JoinHandle is discarded.
    pub fn start_job(&self) -> Result<CacheJob, String> {
        let mut state = self.lock()?;
        if state.lease.is_some() {
            return Err("Cache access is blocked by the active maintenance lease".into());
        }
        state.active_jobs = state
            .active_jobs
            .checked_add(1)
            .ok_or("Cache job counter exhausted")?;
        Ok(CacheJob {
            inner: Arc::clone(&self.inner),
        })
    }

    pub fn begin(&self, worker_id: &str, pending_result: bool) -> Result<String, String> {
        if worker_id != self.worker_id() {
            return Err("Maintenance worker identity does not match".into());
        }
        let mut state = self.lock()?;
        if state.lease.is_some() {
            return Err("A maintenance lease is already active".into());
        }
        if state.active_jobs != 0 || pending_result {
            return Err("Maintenance requires no active cache job or uninstalled result".into());
        }
        let sequence = state
            .next_lease
            .checked_add(1)
            .ok_or("Maintenance lease sequence exhausted")?;
        let token = format!("{}:{sequence}", self.worker_id());
        state.next_lease = sequence;
        state.lease = Some(token.clone());
        Ok(token)
    }

    pub fn end(&self, worker_id: &str, lease_token: &str) -> Result<(), String> {
        if worker_id != self.worker_id() {
            return Err("Maintenance worker identity does not match".into());
        }
        let mut state = self.lock()?;
        if state.lease.as_deref() != Some(lease_token) {
            return Err("Maintenance lease token does not match".into());
        }
        state.lease = None;
        Ok(())
    }

    pub fn snapshot(&self, pending_result: bool) -> Result<Snapshot, String> {
        let state = self.lock()?;
        let quiescent = state.active_jobs == 0 && !pending_result;
        Ok(Snapshot {
            worker_id: self.worker_id().to_owned(),
            active_cache_jobs: state.active_jobs,
            lease_active: state.lease.is_some(),
            pending_result,
            quiescent,
            retirement_allowed: quiescent && state.lease.is_some(),
        })
    }
}

impl Drop for CacheJob {
    fn drop(&mut self) {
        // Never panic while unwinding a failed builder. Recovering the guard
        // here does NOT clear mutex poison; every later operation fails closed.
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        state.active_jobs = state.active_jobs.saturating_sub(1);
    }
}

#[cfg(windows)]
fn worker_entropy() -> Result<[u8; 16], String> {
    #[link(name = "bcrypt")]
    unsafe extern "system" {
        fn BCryptGenRandom(
            algorithm: *mut std::ffi::c_void,
            buffer: *mut u8,
            len: u32,
            flags: u32,
        ) -> i32;
    }
    let mut value = [0; 16];
    // BCRYPT_USE_SYSTEM_PREFERRED_RNG with a null algorithm handle. The OS fills
    // exactly the supplied writable buffer; no engine pointer is involved.
    let status = unsafe {
        BCryptGenRandom(
            std::ptr::null_mut(),
            value.as_mut_ptr(),
            value.len() as u32,
            2,
        )
    };
    if status < 0 {
        return Err(format!(
            "Cannot establish native worker identity: NTSTATUS {status:#x}"
        ));
    }
    Ok(value)
}

#[cfg(not(windows))]
fn worker_entropy() -> Result<[u8; 16], String> {
    use std::io::Read;
    let mut value = [0; 16];
    std::fs::File::open("/dev/urandom")
        .and_then(|mut file| file.read_exact(&mut value))
        .map_err(|e| format!("Cannot establish native worker identity: {e}"))?;
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        panic::{AssertUnwindSafe, catch_unwind},
        sync::mpsc,
        thread,
    };

    fn state() -> Maintenance {
        Maintenance::with_identity("0123456789abcdef0123456789abcdef".into())
    }

    #[test]
    fn lease_blocks_cache_jobs_and_wrong_release_never_clears_it() {
        let state = state();
        assert!(state.begin("other-worker", false).is_err());
        let lease = state.begin(state.worker_id(), false).unwrap();
        assert!(state.snapshot(false).unwrap().retirement_allowed);
        assert!(state.start_job().is_err());
        assert!(state.begin(state.worker_id(), false).is_err());
        for (worker, token) in [
            ("other-worker", lease.as_str()),
            (state.worker_id(), ""),
            (state.worker_id(), "wrong"),
        ] {
            assert!(state.end(worker, token).is_err());
            assert!(state.snapshot(false).unwrap().lease_active);
        }
        state.end(state.worker_id(), &lease).unwrap();
        assert!(state.end(state.worker_id(), &lease).is_err());
        let later = state.begin(state.worker_id(), false).unwrap();
        assert_ne!(lease, later);
        assert!(state.end(state.worker_id(), &lease).is_err());
        state.end(state.worker_id(), &later).unwrap();
        let job = state.start_job().unwrap();
        assert_eq!(state.snapshot(false).unwrap().active_cache_jobs, 1);
        drop(job);
        assert_eq!(state.snapshot(false).unwrap().active_cache_jobs, 0);
    }

    #[test]
    fn finished_but_uninstalled_result_blocks_begin_only() {
        let state = state();
        assert!(state.begin(state.worker_id(), true).is_err());
        // Existing command guards, not this coordinator, decide whether a
        // reset may replace a pending result. Starting it remains permitted.
        let reset = state.start_job().unwrap();
        drop(reset);
        let pending = state.snapshot(true).unwrap();
        assert_eq!(pending.active_cache_jobs, 0);
        assert!(!pending.quiescent);
        assert!(!pending.retirement_allowed);
        let lease = state.begin(state.worker_id(), false).unwrap();
        state.end(state.worker_id(), &lease).unwrap();
    }

    #[test]
    fn detached_job_remains_counted_until_its_closure_finishes() {
        let state = state();
        let permit = state.start_job().unwrap();
        let (release, wait) = mpsc::channel();
        let (done, ended) = mpsc::channel();
        let task = thread::spawn(move || {
            let permit = permit;
            wait.recv().unwrap();
            drop(permit);
            done.send(()).unwrap();
        });
        drop(task); // The original failure: losing this handle is not quiescence.
        assert_eq!(state.snapshot(false).unwrap().active_cache_jobs, 1);
        assert!(state.begin(state.worker_id(), false).is_err());
        // A permitted Session reset can complete while this detached builder
        // is still running. It must not zero or replace the old job count.
        let reset = state.start_job().unwrap();
        assert_eq!(state.snapshot(false).unwrap().active_cache_jobs, 2);
        drop(reset);
        assert_eq!(state.snapshot(false).unwrap().active_cache_jobs, 1);
        assert!(state.begin(state.worker_id(), false).is_err());
        release.send(()).unwrap();
        ended.recv().unwrap();
        assert_eq!(state.snapshot(false).unwrap().active_cache_jobs, 0);
        assert!(state.begin(state.worker_id(), false).is_ok());
    }

    #[test]
    fn panic_releases_job_but_not_the_uninstalled_result_gate() {
        let state = state();
        let permit = state.start_job().unwrap();
        let task = thread::spawn(move || {
            let _permit = permit;
            panic!("synthetic builder panic");
        });
        assert!(task.join().is_err());
        assert_eq!(state.snapshot(true).unwrap().active_cache_jobs, 0);
        assert!(state.begin(state.worker_id(), true).is_err());
        assert!(state.begin(state.worker_id(), false).is_ok());
    }

    #[test]
    fn validation_error_and_sync_panic_release_without_resetting_state() {
        let state = state();
        let failure = || -> Result<(), String> {
            let _permit = state.start_job()?;
            Err("invalid cache input".into())
        };
        assert!(failure().is_err());
        assert_eq!(state.snapshot(false).unwrap().active_cache_jobs, 0);
        assert!(
            catch_unwind(AssertUnwindSafe(|| {
                let _permit = state.start_job().unwrap();
                panic!("synthetic synchronous builder panic");
            }))
            .is_err()
        );
        assert!(state.snapshot(false).unwrap().quiescent);
    }

    #[test]
    fn permit_outlives_other_owner_clones_and_spawn_failure_drop() {
        let state = state();
        let observed = state.clone();
        let permit = state.start_job().unwrap();
        drop(state);
        assert_eq!(observed.snapshot(false).unwrap().active_cache_jobs, 1);
        // A failed OS spawn destroys its captured closure and hence this guard.
        let never_spawned = move || drop(permit);
        drop(never_spawned);
        assert!(observed.snapshot(false).unwrap().quiescent);
    }

    #[test]
    fn poisoned_coordinator_fails_closed_even_after_permit_unwinds() {
        let state = state();
        let permit = state.start_job().unwrap();
        assert!(
            catch_unwind(AssertUnwindSafe(|| {
                let _locked = state.inner.state.lock().unwrap();
                panic!("synthetic internal state failure");
            }))
            .is_err()
        );
        drop(permit);
        assert!(state.snapshot(false).is_err());
        assert!(state.start_job().is_err());
        assert!(state.begin(state.worker_id(), false).is_err());
        assert!(state.end(state.worker_id(), "anything").is_err());
    }

    #[test]
    fn os_worker_identity_is_unique_and_tokens_cannot_cross_workers() {
        let first = Maintenance::new().unwrap();
        let second = Maintenance::new().unwrap();
        assert_eq!(first.worker_id().len(), 32);
        assert_ne!(first.worker_id(), second.worker_id());
        let a = first.begin(first.worker_id(), false).unwrap();
        let b = second.begin(second.worker_id(), false).unwrap();
        assert!(second.end(first.worker_id(), &a).is_err());
        assert!(second.end(second.worker_id(), &a).is_err());
        assert!(second.snapshot(false).unwrap().lease_active);
        second.end(second.worker_id(), &b).unwrap();
    }

    #[test]
    fn concurrent_job_start_and_lease_acquisition_cannot_both_succeed() {
        use std::sync::Barrier;
        for _ in 0..24 {
            let state = state();
            let start = Arc::new(Barrier::new(3));
            let release = Arc::new(Barrier::new(3));
            let (send, receive) = mpsc::channel();
            let job_state = state.clone();
            let job_start = Arc::clone(&start);
            let job_release = Arc::clone(&release);
            let job_send = send.clone();
            let job_thread = thread::spawn(move || {
                job_start.wait();
                let permit = job_state.start_job();
                job_send.send(permit.is_ok()).unwrap();
                job_release.wait();
                drop(permit);
            });
            let lease_state = state.clone();
            let lease_start = Arc::clone(&start);
            let lease_release = Arc::clone(&release);
            let lease_thread = thread::spawn(move || {
                lease_start.wait();
                let lease = lease_state.begin(lease_state.worker_id(), false);
                send.send(lease.is_ok()).unwrap();
                lease_release.wait();
                if let Ok(token) = lease {
                    lease_state.end(lease_state.worker_id(), &token).unwrap();
                }
            });
            start.wait();
            let outcomes = [receive.recv().unwrap(), receive.recv().unwrap()];
            let held = state.snapshot(false).unwrap();
            release.wait();
            job_thread.join().unwrap();
            lease_thread.join().unwrap();
            assert_ne!(outcomes[0], outcomes[1]);
            assert_ne!(held.active_cache_jobs != 0, held.lease_active);
            assert!(state.snapshot(false).unwrap().quiescent);
        }
    }
}
