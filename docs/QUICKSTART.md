# Start here

**0.1.0-main.8 release candidate: native and private full-map checks passed; live gameplay and fresh-install checks remain pending.**

## You need

- Windows 64-bit and Steam RuneScape: Dragonwilds **build 25632050**. Setup checks the actual executable too; another build is refused.
- Your own **Xbox 360 Skate 3 ISO**. You do not need to extract it. The newer PC skate. and PS3 images are not supported inputs.
- An internet connection for the first official tool downloads. Advanced offline input is also available.
- The exact **UE4SS v3.0.1-1152-ge3ba1016** ZIP. Setup provides its official download link and verifies the required files.
- Free disk space for extracted/converted source files and locally generated map data. Final minimum RAM, disk and CPU requirements are not established yet.

The mod includes Python and its map exporter. You do not need to install programming tools. First preparation can be lengthy; prepared data is kept between launches, but the game still has to load it into memory each time.

## Preparation time and space

These historical main.7 measurements come from the development machine. They are not main.8 measurements, a minimum specification or a promised time on your computer:

| Measured task | Result |
| --- | --- |
| First full base-map edge preparation | About **52 minutes 30 seconds** |
| Repeat offline preparation with the base cache ready | **29.83 seconds**, with no edge extraction |
| First captured object/building scene preparation | About **54 minutes 35 seconds**, after the base preparation |
| Repeat offline check of that scene | **23.91 seconds**, with no edge extraction |
| Peak memory during first base preparation | **11.73 GB private memory**, **10.71 GB working set** |

The repeat measurements are offline preparation checks, **not game startup time or FPS**. The first base and captured scene preparations together took about **1 hour 47 minutes**, excluding additional export and verification time. Main.8 adds a separately saved incremental grind graph. In one final native helper run on a private full map, warm initialization took **121.094 seconds**. A single building moved 25 cm updated in **2.828 seconds**, compared with **128.265 seconds** using the retained main.7 worker. This measures that edit, not every building update or game responsiveness. A separate final carrying run used about **14.29 GB private memory after load**, with a **16.14 GB peak pagefile accounting value** after ticks. These are development measurements, not minimum RAM or game FPS. Live gameplay needs separate validation.

Allow space for both preparation files and the installed mod. The measured source ISO was **7.84 GB**, its extraction **6.4 GB**, and the converted data **0.189 GB**. The earlier main.7 unpacked candidate was about **291 MB**; an earlier download ZIP was **129.7 MB**. These are historical package sizes, not final main.8 sizes.

The earlier **5–8 GB installed estimate includes prepared map/grind caches and excludes the ISO and extraction**. It is a planning estimate, not a storage cap. Final usage with the current save is still being measured; retained versions, failed conversion attempts and backups can add space. Minimum hardware requirements are not established.

The earlier isolated cache test used **2.83 GB** for base collision, one captured scene and their generated grind data. This historical set excludes the new incremental graph, installed runtime, converted source assets, building catalogue, backups, other saves and retained older versions.

## 1. Install

1. Close Dragonwilds. Extract the **entire** mod ZIP into its own folder, outside the game directory. Keep that folder for repair or uninstall.
2. Double-click **Setup.cmd**. Do not run it inside the ZIP viewer.
3. Choose your Dragonwilds folder. In Steam, use **Manage → Browse local files** to find it.
4. Open **1 Install** and click **Check game and prerequisites**.
5. If UE4SS is missing, use the official download link in setup, select the downloaded ZIP, and click **Import UE4SS from selected ZIP**. An incompatible existing loader is preserved and reported.
6. Click **Install / update mod**.

## 2. Select your ISO

1. Open **2 Your Skate 3 files**.
2. Browse to your Xbox 360 Skate 3 `.iso`.
3. Click **Prepare my ISO** and wait for it to finish.

Setup downloads the official extraction and conversion tools, checks their exact hashes, extracts the ISO into a private folder, converts the needed data and imports it. Your ISO is preserved and never uploaded. You do not need to locate `default.xex`, extract ROM files yourself, or install programming tools.

Verified tool downloads are cached for reuse. Every conversion attempt has a new private output folder; failed attempts are retained for diagnosis and use extra disk space.

**Optional advanced routes:** the **Advanced files** tab can import already converted assets, use extracted Xbox 360 files, or accept the exact official tool ZIPs for offline preparation. These are alternatives, not requirements for the ISO route.

## 3. Prepare Dragonwilds once

1. Open **3 Prepare and play** and click **Launch game to capture settings**.
2. Enter a solo world and stand on the ground. For this candidate, test on a disposable or backed-up save first.
3. When the setup message says to close the game and prepare the map, close Dragonwilds normally.
4. Back in setup, click **Prepare / resume map**. Leave setup open until it finishes; you can minimize it. Setup shows the current stage and elapsed time. During edge generation it also shows checked, rebuilt and reused section counts with a section percentage. Joining and verification use an animated progress bar because their total work is not known.
5. Click **Prepare building catalogue**, then **Verify readiness**.
6. Click **Play Dragonwilds**.

If preparation is interrupted, reopen the same setup and repeat **Prepare / resume map**. Identical verified inputs resume. A game update or incompatible data is reported instead of silently mixing builds.

The expanded grind-edge cache is prepared once for the installed map and algorithm. Setup shows the number of map sections checked and reused. Updating from an earlier candidate can require this step even when map collision is already prepared; the existing collision export is retained.

Main.8 also prepares a separately versioned incremental grind graph. Follow **Prepare / resume map** and **Verify readiness** when updating; retain your existing private data so compatible collision and edge sections can be reused. Never copy the developer's caches or another save's building data.

A section reaching 100% means that section scan has finished. Joining paths, saving and validation can still follow. Wait for setup's final success result before launching. During a blocking load in game, a temporary message shows the current stage and elapsed time; it disappears when skating is ready. Background work keeps normal play free of a progress banner.

## 4. Skate

1. Enter your solo save. If a loading message appears, wait until it disappears.
2. Open **Mounts**, select **Skateboard**, and click **Equip**.
3. Dismount any ordinary mount, close menus, and briefly hold your normal Dragonwilds mount control (**Alt** by default on keyboard, about a quarter of a second).
4. Use **R** / **Y** / **Triangle** to get on or off the board while staying in skateboard mode.
5. Hold the normal mount control again to return to ordinary Dragonwilds play. **F8** is a fallback.
6. **V** or **right-stick click** cycles High → Low → Dragonwilds cameras.

See [all controls](CONTROLS.md) for pushing, tricks, braking, grabs and controller setup. The spell wheel and inventory radial are blocked during skateboard mode so their controls cannot interrupt skating; they return when you exit the mode.

## Your saves and future launches

Each solo save gets separate building observations. The base map and building catalogue are shared for the supported game build; the mod does not import the developer's save or structures. Supported construction changes update during play. New or changed supporting surfaces can temporarily delay mounting while their collision is accepted.

For continuous grind lines, join the pieces so their collision surfaces meet. Tiny transform rounding and small supported height differences are handled by the edge adapter. A visible joint can still contain a real collision gap or obstruction; the mod does not fill those holes or change the source game's balance and bail rules.

Main.8 includes bounded seam-contact changes. Native replays improved joined-piece coverage, but Fifty-Fifty briefly lost and reacquired contact, and the rightward spin still had a one-frame miss and later exit. Both-direction live grinding remains pending; see [known limits](KNOWN-LIMITS.md).

Use Steam normally after setup. Keep `DragonwildsSkateData` beside the installed mod: it holds your private prepared files and caches. Deleting it forces preparation again. A mod update may need to validate or prepare a new cache; a game update requires a supported release and compatible map data.

## Update or remove

For an update, close the game, extract the new release into a fresh folder, run its **Setup.cmd**, select the same game folder, then **Install / update mod** and **Verify readiness**. Follow any reported preparation steps.

If you edited the advanced keyboard binding file, save your custom bindings separately and restore its original backup before installing the update. Setup protects modified files from being overwritten. Reapply your bindings to the updated file afterward.

To remove the mod, run **Setup.cmd from the extracted release folder**, open **Repair / uninstall**, and choose **Uninstall owned mod files** with Dragonwilds closed. Saves, shared UE4SS files, modified files, backups and private prepared data are preserved. This package has not been tested with Vortex; use the included setup.
