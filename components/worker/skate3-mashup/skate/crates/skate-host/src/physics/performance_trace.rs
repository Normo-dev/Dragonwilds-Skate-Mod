//! Optional phase timings for private simulation profiling. Disabled by default.
//! SKATE3_PERFORMANCE_TICKS=start:end selects inclusive source simulation ticks.
use std::{sync::OnceLock, time::Instant};

static WINDOW: OnceLock<Option<(u64, u64)>> = OnceLock::new();

pub(super) struct Frame {
    clock: Option<Instant>,
    samples: [u128; 9],
    details: [u128; 3],
    tick: u64,
}
impl Frame {
    #[inline]
    pub(super) fn new(tick: u64) -> Self {
        let window = WINDOW.get_or_init(|| {
            let value = std::env::var("SKATE3_PERFORMANCE_TICKS").ok()?;
            let (start, end) = value.split_once(':')?;
            let (start, end) = (start.parse::<u64>().ok()?, end.parse::<u64>().ok()?);
            (start <= end).then_some((start, end))
        });
        Self { clock: window.filter(|&(start, end)| tick >= start && tick <= end)
            .map(|_| Instant::now()), samples: [0; 9], details: [0; 3], tick }
    }
    #[inline]
    pub(super) fn mark(&mut self, index: usize) {
        if let Some(previous) = self.clock {
            let now = Instant::now();
            self.samples[index] = now.duration_since(previous).as_nanos();
            self.clock = Some(now);
        }
    }
    #[inline]
    pub(super) fn begin_detail(&self) -> Option<Instant> {
        self.clock.map(|_| Instant::now())
    }
    #[inline]
    pub(super) fn end_detail(&mut self, index: usize, started: Option<Instant>) {
        if let Some(started) = started { self.details[index] = started.elapsed().as_nanos(); }
    }
    pub(super) fn finish(mut self, state: skate_core::player::state::PhysicalStateId) {
        if self.clock.is_none() { return; }
        self.mark(8);
        let [prepare, queries, animation, input, selection, state_update, solve, finish, output] = self.samples;
        let [biped_update, biped_contact, biped_geometry] = self.details;
        eprintln!("SIMULATION_PERFORMANCE tick={} state={state:?} prepare_ns={prepare} queries_ns={queries} animation_ns={animation} input_ns={input} selection_ns={selection} state_update_ns={state_update} solve_ns={solve} finish_ns={finish} output_ns={output} detail_biped_update_ns={biped_update} detail_biped_contact_ns={biped_contact} detail_biped_geometry_ns={biped_geometry}", self.tick);
    }
}
