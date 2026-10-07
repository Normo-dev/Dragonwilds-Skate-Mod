# Dragonwilds Skate Mod

Source for the Dragonwilds skateboard mod, including its Python/Lua adapter,
native helpers, licensed simulation worker, collision exporters, and original
board artwork.

This is a source repository. It does not include a ready-to-install mod, game
assets, game archives, mappings, saves, or prepared world caches. The current
source inventory and historical release hashes are recorded in `SOURCE-ASSEMBLY.json`.
Assembly does not establish that a development release has passed gameplay tests.

## Develop

Read [BUILDING.md](BUILDING.md), then inspect the build plan:

```powershell
python -B tools/dev.py plan
python -B tools/dev.py verify
```

| Directory | Contents |
| --- | --- |
| `components/adapter` | Python runtime, UE4SS Lua, helper Rust crates, installer and release tools |
| `components/worker` | Worker, licensed engine/host snapshot, exact provenance, patches and tests |
| `components/exporter` | Pinned base collision exporter and CUE4Parse snapshot |
| `components/building-exporter` | Separately pinned building catalogue exporter |
| `artwork` | Original mount icon and board texture |
| `config` | Reviewed game-version-bound building reader rule |
| `notices` | Upstream licenses, dependency inventories, external prerequisite records |
| `provenance` | Original staged release manifest and historical release introduction |

The two exporters intentionally remain separate. Their identities are part of
existing users' prepared-cache validation. Combining them without a migration
would cause needless re-preparation.

## License and credits

Newly authored adapter code is released under **GPL-3.0-only**; see [LICENSE](LICENSE).
Upstream components retain their own licenses and notices. Historical documents
inside unchanged component snapshots may describe the license as proposed; the
author has now authorized this source release. This does not replace any upstream
license or grant rights in game assets.

See [CREDITS.md](CREDITS.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

To upload this source folder into the existing repository, follow
[UPLOADING.md](docs/UPLOADING.md).

