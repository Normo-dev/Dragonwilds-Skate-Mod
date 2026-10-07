# Building from source

## Prerequisites

- Windows x64 and PowerShell 5.1 or later.
- Rust **1.96.0**, Cargo, MSVC C/C++ tools and Windows SDK.
- Python 3.12 with NumPy and Pillow. The recorded validation environment used
  Python 3.12.14 and NumPy 2.3.5. Optional Lua regression tests need Lupa.
- .NET SDK **10.0.401** for exporters, with the package versions in their lockfiles.
- Network access to dependency registries, or populated Cargo/NuGet caches.

The per-component `BUILDING.md` files are authoritative for their exact snapshot.
Build scripts verify their compiler/dependency requirements. No script in this
repository installs into a game unless an explicit installer command is chosen.

## Portable entrypoints

```powershell
python -B tools/dev.py verify
python -B tools/dev.py plan
python -B tools/dev.py test adapter
python -B tools/dev.py build worker --output build/worker
python -B tools/dev.py build helpers --output build/helpers
python -B tools/dev.py build exporter --output build/exporter
python -B tools/dev.py build building-exporter --output build/building-exporter
```

`--powershell` selects a shell executable; `--dotnet` selects the exact SDK executable
for an exporter build. `--offline` is supported for the Rust
builds after dependencies have been fetched. Worker builds run synthetic tests
before compiling; `test worker --output build/worker` runs its test mode alone.
Exporter builds refuse an existing output directory. Builds are not claimed to
be byte-identical across compiler, SDK, or linker versions.

`verify` checks this initial source baseline against the assembly inventory and
rejects added generated/binary/private data. After intentional development edits,
the original inventory remains historical evidence: record and review a new
baseline instead of changing hashes to conceal a mismatch. The worker's own
source verifier likewise requires reviewed corresponding-source inventory updates.

## Tests and fixtures

Worker and component test source is retained in its original tree. Synthetic
fixtures are generated from source by the included build tools. Tests requiring
privately owned game assets remain opt-in and are not run by CI.

Adapter release/installer tests are included in its audited archive. If the final
assembly includes `tests/adapter`, those additional reviewed regression tests are
staged into a temporary portable layout by `test adapter-extra`; runtime modules
are copied alongside them and Lua modules are mapped to `host-probe`. This avoids
embedding a developer workspace path. Extra tests with other fixture requirements
must be explicitly reviewed; the assembler does not copy the developer's work folder.

For an embedded Python interpreter or packages installed with `pip --target`, add
`--test-dependency-path <existing-package-directory>` to the test command. This
supplies that directory explicitly during discovery; it is never copied into the
repository. Standard Python installations can use their normal installed packages.

## Packaging

Read [docs/PACKAGING.md](docs/PACKAGING.md). The repository deliberately has no
ready-made recipe referencing one developer's machine, no bundled runtime
binaries, and no game resources. Compiled output must be verified and designated
before it becomes a package input.

