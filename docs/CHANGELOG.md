# Changelog

## 0.1.0-main.8 — release candidate, not yet published

- Update affected grind components and rail owners after a construction change, retaining the initial native spatial provider and unchanged contact identities.
- Keep verified edge payloads resident with Windows file locks and reuse unchanged payloads. Save separately versioned graph history and periodically compact it, while preserving existing edge/path cache identities.
- Add a bounded seam-contact response for Fifty-Fifty, Boardslide, Tipslide and 5-0. Frozen host replays traverse four joins in Fifty-Fifty with interruption at 297 and reacquisition at 306; left-spin Boardslide is continuous for 162 ticks across three joins. Right-spin Boardslide/Tipslide totals 65 grind ticks across two joins, misses one frame at 300 and exits later at 336. Sustained 5-0 is unproved; Backslash/Darkslide retain the original response. Real gaps, tall obstacles, side walls and rail ends still release.
- Use spatial handplant and footplant queries with original candidate ordering/caps, and memoize exact source triangles/materials with bounded storage and fresh state for each geometry replacement.

The final native gates recorded 246 passed test executions, zero failures and 24 ignored across 30 target results, including three explicit owned-data Session passes. These are execution counts, not unique tests. Physics work is included in this candidate; final private full-map and carrying gates also passed. One 25 cm building move updated in 2.828 seconds versus 128.265 seconds in the retained main.7 worker; warm native initialization took 121.094 seconds. All 240 carrying poses matched the retained baseline exactly. These measured helper results do not establish every edit cost, minimum RAM or game FPS. Native helper replay evidence is not live gameplay acceptance or an in-game FPS claim. Both-direction grinding, fresh installation, restoration and controller acceptance remain pending. Earlier main.7 measurements below are historical.

A pre-final preview3 comparison measured about 11% less mean carrying source work with geometry memoization on, preserving all 240 poses. It is a helper measurement with memoization on/off in one preview build, not a final-artifact or game-FPS result.

## 0.1.0-main.7 — release candidate, not yet published

- Show preparation stages and elapsed time in guided setup and blocking in-game loads. Report section counts and percentages only for known totals; keep joining, saving and verification indeterminate until their result arrives. Clear in-game progress after readiness or cancellation.
- Make an Xbox 360 Skate 3 ISO the standard source input. Guided setup downloads verified tools and handles extraction, conversion and import; manual/existing data routes remain under Advanced files.
- Detect exposed ledges and rails from collision geometry, including short, sloped and curved sections. Exclude flat seams, buried edges, undersides and vertical wall corners.
- Join continuous grind paths across object and cache-section boundaries. Handle transform rounding and small height differences only where collision support is verified; preserve real gaps and obstacles.
- Exclude isolated connected edge groups shorter than 50 cm after joining across objects and map sections. Retain short pieces belonging to longer ledges, curves and branches.
- Replace the Castle-specific edge-height correction with the general support check.
- Save independently versioned grind-edge sections. A construction edit updates affected sections; unchanged sections are retained. Completed preparation is reused across launches.
- Prepare new map sections with up to four workers, while publishing a deterministic, resumable cache. Save the final joined grind paths too, so unchanged launches do not rebuild the full edge graph.
- In crowded exposed-edge queries, prioritize the nearest 40 primitives and preserve their original traversal order. Less crowded and legacy queries retain their original selection. The imported contact tests still decide whether a grind is possible.
- Keep incomplete offline preparation separate from ready data, so it can resume without making a partial map playable. Show section progress in guided setup.
- Require offline repair when missing or damaged fallback data would otherwise trigger a full grind rebuild during gameplay.
- Retain the current and previous accepted generated grind snapshots and attempt bounded cleanup of older owned snapshots. Preserve unknown, corrupt and incomplete data, saves and raw collision geometry; cancel background cleanup for new building work or reload.
- Move large checkpoint validation to a separate helper. Native maintenance blocks new cache work while leaving the resident simulation available; live responsiveness and recovery testing remain pending.
- Record the measured first base preparation (about 52 minutes 30 seconds) and warm offline check (29.83 seconds without extraction), with separate storage and memory guidance. These are development measurements, not game FPS or hardware requirements.
- Add the project source link and GPL-3.0-only license for newly authored adapter code, preserving upstream licenses and notices.

These changes author the host world's grind lines. Imported simulation, animations, inputs and grind-entry/bail rules are retained. Geometry and source-contact checks do not replace the remaining in-game acceptance tests.

## 0.1.0-main.6 — release candidate, not yet published

- Add a guided Windows setup window for installation, separately supplied prerequisites, private source conversion, map/building preparation, repair and uninstall.
- Replace persistent testing text with a delayed, temporary loading message. Ready, source-state and control diagnostics remain in logs.
- Block the spell wheel and inventory radial while skateboard mode owns their controls; restore them on exit. Both blocks have been confirmed in game.
- Reduce repeated triangle work in imported walking collision queries while retaining source physics and query ordering.
- Correct the derived grind-line height on the verified straight Castle double-doorframe arrangement without changing its physical collision mesh.
- Add a complete beginner setup guide, keyboard/controller controls, troubleshooting, limits and credits.

The native carrying replay preserved all recorded poses. Average source simulation time in one paired run changed from 13.24 ms to 8.70 ms; this is a helper measurement, not an in-game FPS result. Live grind, performance, recovery and fresh-install acceptance remain pending.

## Earlier development builds

Added the Mounts entry, original throw-down sequence, Dragonwilds character appearance adapter, high/low/Dragonwilds camera cycle, native controller and keyboard input, persistent whole-map/grind caches, save-specific building observation, incremental scene updates and reload/recovery fixes. Older draft ZIPs and development status files are historical and are not alternate supported editions.
