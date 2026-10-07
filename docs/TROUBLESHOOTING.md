# Troubleshooting

Start with **Setup.cmd → Verify readiness**. Keep the original extracted release folder; repair and uninstall run from there.

| Problem | Next step |
| --- | --- |
| Setup cannot find Python or another bundled file | Extract the entire release ZIP first. Run Setup.cmd from the extracted folder. |
| Wrong game version or loader hash | Use the exact supported game/UE4SS versions. Do not rename or bypass the checks. Setup preserves incompatible shared files. |
| Installation says the game or a helper is running | Exit skateboard mode, close Dragonwilds, then use **Repair / uninstall → Stop mod helper** and retry. |
| Installation was interrupted | With the game closed, choose **Recover interrupted installation** before trying Install again. |
| ISO preparation failed | Check that the ISO is Xbox 360 Skate 3 and that the drive has free space. The failed attempt and log are retained; a retry starts a new private job. You do not need to extract the ISO manually. |
| A tool download failed or its hash differs | Check the internet connection and retry **Prepare my ISO**. Setup will not execute an unverified download. Alternatively, select the exact linked official ZIPs under **Advanced files**. |
| Setup capture or mappings are missing | With the game closed, choose **Refresh first-run capture**. Launch for capture, enter a solo world, wait for its setup instruction, close the game, then prepare the map. |
| First map preparation stopped | Choose **Prepare / resume map** with unchanged game files. Check free disk space and the reported error. |
| Grind cache needs offline preparation or repair | Close Dragonwilds, run **Prepare / resume map**, then **Verify readiness**. Retain the existing data folder; compatible collision and completed edge sections are reused. |
| The board summons an ordinary mount | Select **Skateboard** again, press **Equip**, and check the gold checkmark. Dismount, close menus, then summon. Use Verify readiness if the skateboard entry is absent. |
| Summon does nothing | Finish interacting/travelling, close all menus, stand on a supported surface, and wait for any loading message to finish. Try F8 once. If still blocked, exit the game and check readiness/logs. |
| A built surface is not ready | Stay nearby briefly while the mod observes and updates it. Repeated rebuilding is not normal; preserve logs if the wait continues or repeats without construction changes. |
| A grind ends at a piece joint | Check that the actual collision surfaces meet and that no higher corner or obstacle crosses the path. Report the piece names, rotation and a screenshot; include whether it happens in both directions. Connected object boundaries alone should not end a grind. |
| Wheel controls stay blocked after exiting | Exit using the mount control or F8. If state does not recover, close and restart the game; report the sequence and logs. F10 is a developer recovery control, not a routine player step. |
| The character falls through ground after death or exit | Close the game and report it before continuing that save. This is a release-blocking failure; do not repeatedly fall or delete caches as a workaround. |
| Controller has double inputs | Use one controller route. If testing direct native input, close DS4Windows and check Steam Input routing. See the controls guide for the current test limits. |
| Low frame rate | Compare the same area with skateboard mode off, carrying the board, and riding. Note whether it is a steady slowdown or periodic hitch. Include game graphics settings and CPU/GPU/RAM when reporting. |

## Where to find evidence

Inside the selected game folder:

- `DragonwildsSkateData/logs/relay.log`: adapter and simulation startup.
- `DragonwildsSkateData/logs/skate-relay-native.log`: native simulation and collision information.
- `DragonwildsSkateData/logs/skate-cache-worker.log`: object/cache preparation.
- `DragonwildsSkateData/map-data/setup-logs`: offline export/preparation logs.
- `RSDragonwilds/Binaries/Win64/ue4ss/UE4SS.log`: host scripts and restoration errors.

Give the mod version, game build, the last action before the failure, whether you were carrying or riding, and whether the area has player construction. Logs can contain local paths and save identifiers; review them before sharing. Do not upload your ISO, extracted assets, complete cache folder or save without intending to share those files.

## What persists

Prepared map and grind files persist on disk. Per-save building observations and verified scene caches are reused; construction changes update the affected scene. RAM is released when the helper exits, so each launch still has a loading phase. Repeated five-minute waits on an unchanged prepared scene are a bug, not an expected mounting step.

### Automatic cache cleanup

The candidate keeps the **current and previous accepted generated grind snapshots**. It can remove older snapshots recorded as its own, while preserving files that are unknown, corrupt or incomplete. Cleanup never deletes saves or raw collision geometry.

Cleanup is best effort: it has an **8-second startup budget** and a **60-second background budget**, and yields to new building work or a reload. Reaching the budget can leave older files for a later pass; it is not a readiness failure. Large checkpoint validation runs in a separate helper. Live cleanup and gameplay responsiveness still need the final in-game test.

Uninstall preserves private data so reinstalling can reuse it. Shared loader files and unknown/modified files are not automatically removed. Keep retained folders until you are sure you no longer need their data or backups.
