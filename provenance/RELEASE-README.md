# Dragonwilds Skate

A skateboard mount for RuneScape: Dragonwilds, using the imported Skate 3 simulation and animations with your Dragonwilds character and world.

**0.1.0-main.8 release candidate — not yet published.** This build includes physics and performance changes. Final native tests and private full-map/carrying checks passed; fresh-install, live gameplay, controller and stability checks remain; see [known limits](docs/KNOWN-LIMITS.md). Older draft ZIPs in the development folder are historical, not alternate editions.

## Start

Extract the complete candidate ZIP into a separate folder and double-click **Setup.cmd**. The guided setup installs the mod, checks external prerequisites, helps convert your own source files, and prepares Dragonwilds collision locally. Read [Start here](docs/QUICKSTART.md).

Requires Windows 64-bit, Steam Dragonwilds **build 25632050**, the exact **UE4SS v3.0.1-1152-ge3ba1016** prerequisite, and your own **Xbox 360 Skate 3 ISO**. Setup downloads verified tools and extracts/converts the ISO locally; no manual ROM extraction is needed. Python and the map exporter are included. No game assets, maps, saves, converters or developer collision caches are bundled.

The earlier main.7 full base-map edge preparation took **about 52 minutes 30 seconds on the development machine**. Preparation is resumable and saved for reuse. That historical measurement is not a main.8 setup or startup promise; see [preparation and storage guidance](docs/QUICKSTART.md#preparation-time-and-space).

## Play

- Equip **Skateboard** in Mounts, close menus, then briefly hold your normal mount control to enter or exit skateboard mode.
- **R / tap Y / tap Triangle** gets on or off the board within that mode.
- **V / right-stick click** cycles High → Low → Dragonwilds cameras.
- Base map and grind preparation persist on disk. Supported player-building changes update separately for each solo save.
- Exposed ledges and rails are detected from collision geometry. Connected pieces can share a continuous grind line; sloped and curved edges are included. Isolated groups shorter than 50 cm are excluded after joining, while short pieces connected into a longer ledge remain. Original grind entry and bail rules still apply.
- Small supported construction edits update affected grind components while retaining unchanged rails. Bounded seam-contact changes improve native replay coverage, but some grinds still interrupt or exit. See [the exact replay limits](docs/KNOWN-LIMITS.md); live connected-piece grinding remains pending.
- Normal play has no persistent testing banner. Blocking loads show the current stage and elapsed time, then disappear when ready. Section counts and percentages appear when a section total is known.

## Guides

- [Installation, your own saves, updates and uninstall](docs/QUICKSTART.md)
- [Every default keyboard/controller mapping](docs/CONTROLS.md)
- [Troubleshooting and logs](docs/TROUBLESHOOTING.md)
- [Current limits and remaining acceptance checks](docs/KNOWN-LIMITS.md)
- [Changes](docs/CHANGELOG.md)
- [Credits and source](docs/CREDITS.md)

Solo play only. Use the included setup; Vortex installation has not been tested. Source archives, build instructions and dependency notices are included in the candidate. This unofficial mod operates locally; your converted assets and generated world data stay on your computer.

Full-map resident data uses substantial memory: the recorded final native run used about 14.29 GB private memory after load. Minimum RAM and in-game carrying/riding FPS are not established. See the measured load/update timings in [the guide](docs/QUICKSTART.md#preparation-time-and-space); native helper results do not establish live gameplay acceptance.

[Project and source on GitHub](https://github.com/Normo-dev/Dragonwilds-Skate-Mod). Newly authored adapter code is GPL-3.0-only; upstream components retain their own licenses.
