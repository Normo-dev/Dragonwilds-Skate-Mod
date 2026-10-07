# Credits and distribution

Dragonwilds Skate is an unofficial community mod. RuneScape: Dragonwilds and Skate 3 remain their respective owners' games.

- **Universal Modder**, by rehan-remade: modding workflow and the Skate import example.
- **Skate 3 Rust Engine / SK8-ENGINE**, including dumbad's research and Chasm's rewrite, and the **2010 Rust Rewrite Mashup** host: source skating simulation and animation integration. Retained upstream licenses and attribution are included with the corresponding source.
- **UE4SS**: external Unreal loader prerequisite.
- **CUE4Parse** and the managed export dependencies: local game asset/collision reading.
- **SDL3**: native controller input.
- **Python, NumPy, Pillow, Rust** and other dependencies: runtime, conversion support, native helpers and build tooling. Exact notices are in `notices/`.
- **[antangelo xdvdfs](https://github.com/antangelo/xdvdfs)** (MIT) and the upstream mashup converter: separately downloaded tools used on the player's own files; neither is bundled.

The menu icon and custom deck artwork were created for this mod under the author's art direction.

The project source is at **[Normo-dev/Dragonwilds-Skate-Mod](https://github.com/Normo-dev/Dragonwilds-Skate-Mod)**. The candidate also includes corresponding source archives and build instructions under `sources/`, matching its bundled components.

Newly authored adapter code is licensed under **GPL-3.0-only**, as authorized by its author. Upstream components retain their own licenses and attribution; consult the exact included notices. This code license does not grant rights to either game's assets.

## What the download contains

The adapter, simulation worker, loader-facing scripts, native helpers, managed exporters, a bundled Python runtime, custom artwork, setup tools, documentation and corresponding source/notices.

## What stays on the player's computer

Either game's original or converted assets, maps, collision exports, grind caches, mappings, ISO contents, save data and runtime diagnostics. Setup creates or imports these privately from the player's own files. UE4SS and the source converter/extractor are separate verified downloads.

The installed adapter operates locally. Setup links open official prerequisite downloads in the user's browser; it does not request an account or upload the player's files.
