// Compile the actual host grind modules and their tests in isolation: this
// extracted host package lacks unrelated retail playback test fixtures.
#[path = "../../skate3-mashup/skate/crates/skate-host/src/grind_world/octree.rs"]
mod octree;
#[path = "../../skate3-mashup/skate/crates/skate-host/src/grind_world/spline.rs"]
mod spline;
#[path = "../../skate3-mashup/skate/crates/skate-host/src/grind_world/provider.rs"]
mod provider;
use provider::StaticProvider;
use spline::primitives;
#[path = "../../skate3-mashup/skate/crates/skate-host/src/grind_world/tests.rs"]
mod tests;
