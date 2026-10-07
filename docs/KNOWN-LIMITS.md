# Release candidate limits

This is the 0.1.0-main.8 release candidate. It has not yet passed the full public-release acceptance run.

## Supported scope

- Windows 64-bit, Steam Dragonwilds build **25632050**, exact executable and UE4SS build checked by setup.
- Solo play. Multiplayer synchronization is unsupported.
- Imported Xbox 360 Skate 3 source simulation and animations. Source game files must be prepared locally.
- Whole base-world collision with separately observed player-building and object updates. No developer world data is bundled.

## Checks still required before publication

- A fresh guided installation, source conversion and preparation on a separate setup, including a different solo save.
- A continuous route across the map with loading boundaries, terrain, authored objects and built structures.
- Place, move and remove supported floors/walls; confirm riding collision and grinding update and remount stays responsive.
- Death, respawn, menus, controller disconnect, game exit and restart restore ordinary character collision, input and camera state.
- Keyboard and direct native PS5/Xbox controller tests, including supported wired/wireless routes and no duplicate input.
- Carrying/riding game FPS, appearance under varied lighting, three camera modes, and original throw-down sequence.
- Continuous grinds across several joined pieces, including rotated Castle doorway edges and other short, sloped or curved ledges, need a live grind pass.
- Background cache cleanup, cancellation for new construction/reload, and large checkpoint validation need an in-game responsiveness and recovery pass.

## Current limitations

- The frozen host response covers Fifty-Fifty, Boardslide, Tipslide and 5-0 contacts. Final native tests and private full-map/carrying checks passed; live acceptance remains pending. The replay results below include interruptions and exits; they do not prove perfect joined-piece grinding.
- Main.8 retains a full resident grind graph and native provider. The final private full-map helper measured 121.094 seconds for warm initialization and 2.828 seconds for one 25 cm building move, versus 128.265 seconds in the retained main.7 worker. The separate carrying run used about 14.29 GB private memory after load and reached 16.14 GB peak pagefile accounting after ticks. These do not establish minimum RAM, other edit costs or hardware suitability.
- Minimum hardware requirements and a worst-case preparation time have not been established.
- Historical main.7 first base preparation took about 52 minutes 30 seconds and peaked at 11.73 GB private memory on the development machine. The first captured building scene took another 54 minutes 35 seconds. Warm offline checks took 29.83 seconds for the base and 23.91 seconds for that scene without extraction. These checks establish earlier cache reuse, not main.8 startup time, FPS or minimum RAM. That historical isolated cache set used 2.83 GB and excludes the new incremental graph; it is not a complete installation or storage cap.
- Persistent data removes repeated export work; each process still loads and verifies runtime data. A new worker or game build can invalidate a cache.
- Cleanup retains the current and previous accepted generated grind snapshots. Unknown, corrupt and incomplete files are preserved, so automatic cleanup does not impose a disk-space cap.
- Object discovery can cause a periodic hitch. The recorded carrying optimization reduces source simulation work; it is not proof of a particular game FPS.
- Grind lines follow the supplied collision geometry. Flat tile seams, buried edges, inaccessible undersides and near-vertical wall corners are excluded. A visual edge without corresponding collision cannot be made reliable by edge detection alone.
- After edges are joined across objects and map sections, isolated connected groups totaling less than 50 cm are excluded. Short segments in a longer connected group remain, including curves, corners and branches. This avoids loading large numbers of isolated decorative fragments.
- The imported grind-entry and bail rules remain in effect. Main.8 adds a narrowly bounded host response for proved decorative seam contacts during an engaged grind. Very short edges, abrupt turns, real gaps and obstacles can prevent a grind or end one. Crowded exposed-edge queries prioritize nearby rails within the 40-primitive limit; this cannot guarantee every possible contact among overlapping structures.
- One-sided or inward-facing collision meshes can expose a geometric edge that the original collision probes cannot support. The adapter does not reverse physical collision or fill missing surfaces.
- Controller support uses SDL and does not require DS4Windows by design, but the full device/connection matrix is not yet playtested.
- Vortex installation has not been tested. Use Setup.cmd.
- Exact external download artifacts must remain available. If a linked prerequisite is unavailable, setup needs an updated verified release; a different download is not interchangeable.

Errors remain in local logs. Normal play has no persistent diagnostic HUD; a temporary message appears only for a blocking preparation or restoration wait. Initial installation still displays the functional setup instruction in the game.

## Frozen host replay results

These replay results used the original source Session with privately supplied geometry and assets. The final native gates also passed three explicit owned-data Session tests. Neither establishes live game acceptance.

| Replay | Recorded grind | Remaining limit |
| --- | --- | --- |
| Fifty-Fifty | 154 total grind ticks; four joins traversed | Interrupted at tick 297, remained out through 305, reacquired at 306 |
| Left-spin Boardslide | 162 continuous grind ticks across three joins | Limited to the tested arrangement and input |
| Right-spin Boardslide → Tipslide | 65 total grind ticks across two joins | One-frame miss at 300, reacquired at 301; later exit at 336 |

Left/right refer to takeoff spin, not travel direction; both recorded board-spin replays travel the same way along the rail. Reverse traversal still needs live acceptance.

5-0 (FiveO) occurred for only one frame in the Fifty-Fifty replay; sustained 5-0 seam grinding is unproved. Backslash and Darkslide retain the original response and have no proved rough-seam tolerance. Sloped/curved geometry checks do not establish Session continuity on every such surface. Sixteen negative controls preserved release at tall obstacles, real gaps, rail ends and side walls.

In a **pre-final preview3 helper comparison**, geometry memoization reduced mean carrying source work by **about 11%**, with all 240 recorded poses identical. This compares the same preview worker with memoization on/off; it is not a final-artifact result or in-game FPS benchmark. The final worker preserved all 240 full-map carrying poses exactly against the retained baseline. Mean helper requests took 5.68 ms idle and 5.62 ms running, including protocol overhead. No final memoization on/off comparison or game-FPS claim is established.
