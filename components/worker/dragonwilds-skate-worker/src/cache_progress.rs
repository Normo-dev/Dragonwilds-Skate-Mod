//! Human-facing cache telemetry only; never writes to the JSON response pipe.
use std::time::Instant;

fn line(operation: &str, phase: &str, state: &str, elapsed_ms: u128) -> String {
    format!("DWS_CACHE_PHASE operation={operation} phase={phase} state={state} elapsed_ms={elapsed_ms}")
}

fn run_with<T>(operation: &str, phase: &str, work: impl FnOnce() -> Result<T, String>,
    mut emit: impl FnMut(String)) -> Result<T, String> {
    emit(line(operation, phase, "start", 0));
    let started = Instant::now();
    let result = work();
    emit(line(operation, phase, if result.is_ok() { "end" } else { "error" }, started.elapsed().as_millis()));
    result
}

pub fn run<T>(operation: &str, phase: &str, work: impl FnOnce() -> Result<T, String>) -> Result<T, String> {
    run_with(operation, phase, work, |line| eprintln!("{line}"))
}

/// Call only after durable offline completion or successful native acceptance.
pub fn ready(operation: &str, started: Instant) {
    eprintln!("{}", line(operation, "ready", "end", started.elapsed().as_millis()));
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn success_has_start_and_end_and_preserves_the_value() {
        let mut events = Vec::new();
        let value = run_with("scene", "building_runtime_provider", || Ok(42), |line| events.push(line));
        assert_eq!(value, Ok(42));
        assert_eq!(events.len(), 2);
        assert_eq!(events[0], "DWS_CACHE_PHASE operation=scene phase=building_runtime_provider state=start elapsed_ms=0");
        assert!(events[1].starts_with("DWS_CACHE_PHASE operation=scene phase=building_runtime_provider state=end elapsed_ms="));
    }
    #[test]
    fn failure_is_not_reported_as_completion_or_ready() {
        let mut events = Vec::new();
        let value = run_with::<()>("prepare", "joining_grind_paths", || Err("original error".into()), |line| events.push(line));
        assert_eq!(value, Err("original error".into()));
        assert_eq!(events.len(), 2);
        assert!(events[1].starts_with("DWS_CACHE_PHASE operation=prepare phase=joining_grind_paths state=error elapsed_ms="));
        assert!(!events.iter().any(|line| line.contains("phase=ready")));
    }
}
