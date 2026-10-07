# Portable installer foundation

`skate_install.py` is staged code, not an installable mod release. This folder supplies no real release manifest, loader, game assets, worker binary, exporter, or Python distribution. Its tests create clearly marked fake payloads in temporary folders and remove those fixtures afterward. No live game installation is used.

The release builder should place this module at `app/skate_install.py`, alongside `skate_preflight.py` and `runtime_paths.py`. The extracted package's Python must be able to run that script. The installer inserts its app directory into `sys.path`, including for an isolated embedded Python runtime.

## Builder interface

The existing `release-manifest.json` schema stays at 1. Its `product` is `DragonwildsSkate`; `version` is a nonempty string; every payload record has `path`, `sha256`, and `bytes`. Compatibility must specify the tested game EXE hash, UE4SS DLL hash, and Steam build. Every install source must be one of those exact listed and checked payload files.

Add an `install` object with these fields:

| Field | Contract |
| --- | --- |
| `schema` | Integer `1` |
| `runtime` | Mapping from runtime key to a package-relative payload path |
| `host_files` | List of `{source, destination}`; destination is relative to this mod's `Scripts` directory |
| `loader_files` | List of `{source, destination}` for exact bundled shared files or optional fresh defaults; destination is relative to `RSDragonwilds/Binaries/Win64` |
| `external_prerequisites` | Optional list of `{destination, sha256, bytes}` identifying the three exact manually installed UE4SS files; never copied or removed |

Required runtime keys: `worker`, `transport_library`, `lua_transport_library`, `render_library`, `map_export`, `gamepad_library`, `mount_icon`, `python_runtime`, `relay_launcher`. Optional keys: `dotnet`, `board_deck_texture`, `building_reader_library`, `building_export`, `building_rule`. A DLL map exporter requires `dotnet`; the portable EXE exporter does not. The optional building reader must be a DLL and the supplementary building exporter must be a self-contained EXE. A building rule must be `config/building-collision-rule.json` and requires both building tools. The launcher path must be `app/skate_launcher.py`. Every mapped payload must exist, be nonempty, and match the manifest. Required app modules include `runtime_paths.py`, `skate_preflight.py`, `skate_launcher.py`, and `skate_install.py`.

The generated Lua settings include the verified `game_exe_sha256` and, if
supplied, the relocated `building_reader_library`. The supplementary exporter is
an offline Python runtime path. Supplying these files does not turn on building
collision or change the existing private map, collision, or grind caches.

Host entries must be Lua files and include `main.lua`. They cannot supply `skate_config.lua`, which is generated locally. `app/runtime.json` and `release-manifest.json` must not appear as payload records: runtime settings are generated, while the manifest itself is copied separately.

The draft package uses **external prerequisites**, because compiled UE4SS/UEPseudo redistribution terms have not been established. It includes no loader binaries or UEHelpers source. The external prerequisite list must identify all three required paths:

- `dwmapi.dll`
- `ue4ss/UE4SS.dll`
- `ue4ss/Mods/shared/UEHelpers/UEHelpers.lua`

All three prerequisites must already exist with the exact recorded SHA256 and byte count. Missing or wrong files refuse the plan before any installation mutation. They are checked again after the transaction and are never owned, copied or deleted by setup. `loader_files` may still supply the fresh reviewed default INI; existing shared settings remain unchanged.

The older bundled-framework mode remains implemented and tested, but is not used by this public draft. In that mode, other explicitly listed `.lua` files under `ue4ss/Mods/shared/` are permitted as shared framework dependencies. Only these two tested binary destinations are accepted. A different proxy architecture requires a future installer change.

Optional default configurations can target `ue4ss/UE4SS-settings.ini` or `ue4ss/Mods/mods.txt`. Existing copies are preserved byte-for-byte, even when they differ from the supplied defaults. Existing binaries and shared Lua files must exactly match the supplied hashes; setup refuses to replace them. Freshly installed framework files are treated as shared thereafter and retained on uninstall.

## Installed layout

For an explicitly selected and verified game root:

- `DragonwildsSkate/`: exact listed release files plus its manifest and generated `app/runtime.json`.
- `RSDragonwilds/Binaries/Win64/ue4ss/Mods/DragonwildsSkate/Scripts/`: listed host Lua and generated `skate_config.lua`.
- The mod's own `enabled.txt`: enables this entrypoint after payload/configuration writes.
- `DragonwildsSkateData/assets`, `map-data`, and `mailbox`: paths reserved for private first-run preparation and runtime data. The installer does not populate or delete their contents.
- `DragonwildsSkateData/install/`: ownership marker, lock, SHA-256 receipt, and retained transaction journals/backups.

Runtime and Lua configuration uses the actual resolved installation paths. It includes the launcher/Python paths and the Lua transport library, with trailing separators on Lua mailbox/assets/map-data paths, the selected game root and exact Steam build. No developer workspace paths or imported asset locations are copied into generated settings.

An enabled `DragonwildsSkateProbe`, whether detected through its marker or `mods.txt`, blocks installation with a migration message. The installer does not alter the prototype's files or edit the shared mod list.

## Operations

Using Python from the extracted release, outside the installed `DragonwildsSkate` directory:

```text
python app/skate_install.py plan --package <extracted-release> --game-root <Dragonwilds-game-folder>
python app/skate_install.py install --package <extracted-release> --game-root <Dragonwilds-game-folder>
python app/skate_install.py uninstall --game-root <Dragonwilds-game-folder>
python app/skate_install.py recover --game-root <Dragonwilds-game-folder>
```

`plan` is read-only. `install` verifies the payload, EXE, Steam build, required loader hashes, and ownership before applying changes. Close Dragonwilds and its mod helpers before updating or uninstalling; in-use files cause failure and rollback. Setup never terminates a process. Running uninstall through the installed Python is refused before mutation, because Windows would lock that owned executable.

Unowned file collisions are refused even if their bytes match the release. Existing mod-owned files can be replaced only when their current hashes still match the receipt. Replaced and removed owned files are backed up before mutation. Obsolete files from an earlier owned release are removed under the same checks.

Each transaction stages all payload bytes and backups, writes its journal, applies checked operations, and writes the new receipt last. Exceptions restore the prior matching state. A normal installation retry recovers an interrupted known transaction before checking ownership; `recover` is also available explicitly. Unknown independent edits stop recovery instead of being overwritten, and the backups remain available. This is process-interruption recovery, not a promise against storage-device failure.

Uninstall deletes only files whose hashes match the owned receipt. It retains modified files, unrelated files, framework/loader files, shared configurations, prepared assets, map caches, backups, logs, and saves. Its JSON reports `uninstalled_with_preserved_files` when applicable and specifically reports a preserved modified enable marker. It does not recursively delete directories.

Every mutation path is confined to the selected package/mod/framework/state areas under the verified game root. Traversal, Windows reserved/ambiguous filenames, symlinks, and junctions below that root are rejected. Backups and receipt paths undergo the same checks. The game root itself is resolved from the user's explicit selection.

## Verification scope

`test_skate_install.py` exercises external prerequisite absence/mismatch/exact reuse and preservation, relocated Unicode/space paths, compatible shared reuse, foreign collisions, owned upgrades/backups, obsolete-file removal, modified/private/save preservation, transaction failure rollback, receipt restoration, interrupted install/upgrade retry, explicit recovery, unknown edits, prototype refusal, incomplete payload gates, optional texture handling, path/receipt escape rejection, a real temporary Windows junction, in-use-runtime refusal, and concurrent setup exclusion. The fixtures contain no runnable game/loader files.

A completed allowlist-built release and a clean real-game installation test remain separate gates. These installer tests do not assert that the entire public mod package has been assembled or released.
