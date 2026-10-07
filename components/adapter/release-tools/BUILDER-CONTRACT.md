# Allowlist package staging

`build_release.py stage --recipe <private-json> --output <new-folder>` creates a
reviewable folder, never a release ZIP or upload. `audit --package <folder>`
rechecks hashes, exact inventory, source archives and private-path exclusions.
An existing stage is never overwritten. Failed writes remain as evidence.

The current package status is always
`draft_pending_publish_approval_and_public_playtests`. A successful audit does
not establish in-game acceptance or verify a player's asset conversion.

## Private recipe schema 1

Input paths are private build locations. They are never copied into the public
manifest. Binary/artwork records are `{path, sha256, label}`; `label` is a short
relative provenance description, never an absolute path. SHA values are lowercase.

| Key | Value |
| --- | --- |
| `schema`, `version` | `1` and a filename-safe version such as `0.1.0-draft.1` |
| `app_root`, `lua_root` | Directories containing runtime Python and Lua source |
| `bootstrap`, `installer` | Exact bootstrap Lua and installer Python files |
| `own_license` | `{path, sha256}` for the GPL-3.0-only license text |
| `worker` | `binary` record, `source_zip` path and `source_sha256` |
| `helpers.ipc`, `helpers.render` | `binary` record, `source_root`, `source_sha256`, `notices_root` |
| `helpers.buildings` | Optional read-only building reader with the same exact binary/source/notices fields |
| `exporter` | `root`, `inventory` JSON, `source_zip`, `source_sha256`, `notices_root` |
| `building_exporter` | Optional supplementary catalogue exporter with the same fields; staged separately under `building-exporter/` |
| `building_rule` | Optional `{path, sha256, label}` for the reviewed executable-bound `S3BUILDINGRULE1`; requires both building tools |
| `python` | Prepared `root`, official archive `sources` JSON, `downloads` directory |
| `sdl` | `binary` record and text-only `notices_root` |
| `artwork` | Required `mount_icon` record, optional `board_deck_texture` record |
| `loader` | `mode: external`, `files` mapping described below, text-only `notices_root` |
| `compatibility` | JSON path with `steam_build`, `game_exe_sha256`, `ue4ss_sha256` |
| `documents` | Optional records with an additional `destination: docs/<name>.md` |
| `readme` | Optional exact record replacing the historical fallback root introduction |
| `wizard` | Optional mapping containing exactly `Setup.cmd` and `setup-wizard.ps1` records; requires `app_root/skate_wizard.py` and sibling `test_skate_wizard.py` beside the launcher input |

The public draft uses **external loader mode**. Its three records identify
`dwmapi.dll`, `ue4ss/UE4SS.dll` and
`ue4ss/Mods/shared/UEHelpers/UEHelpers.lua`. The builder checks those input hashes
but copies none of their bytes. Their exact size/hash become manual prerequisites
that the installer checks without taking ownership. Only the optional reviewed
fresh `ue4ss/UE4SS-settings.ini` is copied; an existing shared file is preserved.
The older `bundled` mode remains a tested implementation path, not the draft's
distribution choice. No compiled UE4SS/UEPseudo redistribution clearance is claimed.

The exporter inventory is schema 1 with exact `files` records (`path`, `bytes`,
`sha256`). Only listed files are selected. PDBs, proprietary decoder DLLs and game
resource payloads are prohibited. Managed `OodleSharp.dll` and `Oodle.NET.dll` are
separately noticed managed dependencies, not the proprietary native decoder.

The supplementary exporter leaves the base `exporter/` inventory unchanged, so
adding building metadata does not alter the prepared terrain/map identity.
Its executable becomes runtime path `building_export`; the reader DLL becomes
`building_reader_library`. The installer passes only the reader path and the
verified game executable hash to Lua. Including these tools does not enable
building collision. The owned catalogue and supplement marker are private setup
outputs and are rejected from public payloads and source archives.

The optional rule is installed at `config/building-collision-rule.json` and
exposed as runtime path `building_rule`. Its verified game hash must equal the
release compatibility hash. Supplement-aware setup/preflight is selected with
`prepare_main_recipe.py --setup-source-root <reviewed-app-source>`; legacy map
preparation still uses the unchanged base exporter and geometry modules.

Run `skate_setup.py prepare-buildings` with the game/helpers closed to prepare
only the catalogue. `import-buildings <source-map-data>` accepts an existing
verified supplement with identical base, game, mappings, exporter and rule pins.
Both publish an independent `building-supplement.json`; content-addressed
catalogue files make its replacement atomic. They never replace the base map,
its setup ownership marker, terrain binaries, original grind file, or accepted
scene caches. An already matching supplement does not export again.

Helper sources must be self-contained under their `helpers/<component>` folder.
The building reader includes its own exact loader constants. The helper rebuild
script reads the reviewed `SOURCE-CLOSURE.json` list, accepting only `ipc`,
`render`, and optional `buildings`; every included helper retains its binary and
compiled-source fingerprint in the closure and carries its own notices.

## Collected source and runtime closure

Python starts at `skate_launcher`, `skate_setup` and `skate_cache_worker`, follows
imports and literal sibling worker filenames, and permits only the standard
library plus NumPy/Pillow externally. Lua starts at the bootstrap and production
bridge, follows literal `require`/`pcall(require,...)`, and permits only generated
`skate_config` and external prerequisite `UEHelpers`. Dynamic module names or
diagnostic entrypoints fail collection. `ground_probe` is an intentional runtime
recovery module; captured probe data is excluded.

When the guided setup is selected, Python also starts at `skate_wizard` and follows
its imports. `skate_install` is always bound to the exact reviewed installer input,
not a stale sibling copy. The launcher, Windows Forms script and backend are
included as exact corresponding source, with the wizard's isolated tests. Private
download-availability evidence is not selected. A reviewed root README and explicit
user guides replace historical development instructions in the release candidate.

Every selected embedded Python/NumPy/Pillow byte is compared with the official
hash-verified archive. Test/example files are excluded. The generated `._pth`
explicitly includes `../app`, with the standard-library ZIP and site-packages.
The source archive contains exact shipped Python/Lua source, native helper source
and lockfiles, build scripts, installer/builder tests and the adapter license.
Worker/exporter corresponding-source archives retain their own provenance.

Public outputs contain no maps, game archives, mappings, imported character/game
assets, saves, private caches/mailboxes, converter, game config captures, developer
runtime, build PDBs or absolute project paths. Two immutable published NuGet DLLs
have reviewed upstream-author PDB path exceptions bound to destination plus SHA;
the author's current user/workspace paths cannot use those exceptions. Official
NumPy public CI runner paths are retained unchanged with archive provenance.

## Verification

`test_build_release.py` checks full fake staging, relocation with spaces/Unicode,
optional assets, exact external prerequisites without bundled bytes, import and
require closure, archive/path exclusions, source hash failures, tampering and
extra files, official-runtime byte substitution, and helper build argument,
compiler binding and environment restoration. It does not install into a game.

The final stage must additionally pass Universal Modder's publish check and an
explicit warning review. Its source archives also require content review; the
tool does not recursively inspect compressed source. Publishing is the author's
separate decision after a concrete candidate and in-game test.
