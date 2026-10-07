# Building the worker

This source tree contains the worker, its host adapters, and the exact licensed engine snapshot plus the patches inventoried in `SOURCE-PROVENANCE.json`. It does not contain a converter, game assets, terrain caches, captured saves, third-party tools, or registry dependency source archives. Cargo retrieves the registry sources identified by the retained lockfile versions and SHA-256 checksums. Keep this source tree, all notices, and the provenance reports with a distribution of the covered worker.

## Requirements

- Windows x64, PowerShell 5.1 or later.
- Rust 1.96.0 with the `x86_64-pc-windows-msvc` target, Cargo, and the Microsoft C/C++ build tools and Windows SDK. The `zstd-sys` and BLAKE3 dependencies compile native code.
- Python 3 with NumPy for the synthetic numeric test fixtures. Verification used Python 3.12.14 and NumPy 2.3.5. The worker runtime itself does not embed Python.
- Enough disk space for Cargo dependencies and build output; place the build directory on another drive if needed.

The compiler and SDK are prerequisites; they are not included. This package does not include Microsoft runtime DLLs or third-party build tools.

## Commands

Run in this folder:

```powershell
# Inspect arguments without executing tools or modifying files.
.\Build-Worker.ps1 -Mode Plan -TargetDirectory 'D:\Skate build'

# Verify the source inventory and download exact locked registry dependencies.
.\Build-Worker.ps1 -Mode Fetch

# Generate numeric fixtures, run worker tests, and build the worker.
.\Build-Worker.ps1 -TargetDirectory 'D:\Skate build' -Offline
```

`-CargoExecutable`, `-RustcExecutable`, and `-PythonExecutable` accept explicit executable paths. They are invoked as argument arrays, so paths containing spaces work. `-Profile release` builds a release profile; the verified artifacts used the optimized debug profile specified in the worker manifest. A different profile or compiler changes the artifact and needs its own verification.

The default build runs the worker tests before producing `x86_64-pc-windows-msvc/debug/dragonwilds-skate-worker.exe` below the selected target directory. It prints the resulting SHA-256. `-Mode Test` runs the same numeric fixture and test steps without the final binary build. Private game-data tests remain ignored unless explicitly selected with locally owned data.

The script requires Rust 1.96.0, binds Cargo to that checked compiler, and passes `--locked`; it will fail instead of silently changing dependencies. Caller compiler wrappers are temporarily cleared and then restored. `-Offline` forbids registry access and requires a prepared Cargo cache. The included lockfiles identify registry sources, but this is not a self-contained offline dependency archive.

## Paths and artifact identity

The script maps Rust source paths to `/source/dragonwilds-skate` and Cargo registry paths to `/cargo/registry`. Set `-RemapPrefix` or `-RegistryDirectory` to override these values. Both Windows and slash-normalized paths are mapped. It temporarily replaces caller Rust flags and disables incremental compilation, then restores the environment even if a command fails.

These flags configure Rust path remapping. They do not promise removal of every string produced by a native compiler or linker, and do not make builds byte-for-byte reproducible across compiler/SDK versions. The archived validation binaries were built before this script and did not use these remap flags. A remapped build must record its own hash and runtime checks.

## Additional engine tests and known limitations

From `skate3-mashup/skate`, generate the licensed numeric collision fixture with `python tools/make_collision_fixture.py`, then run `cargo test --locked --workspace --lib --tests`. Four retained examples need the separate game UI crate, so the explicit library/test selection is required.

`UNIT-BASELINE-REPORT.json` records the two engine assertions that fail identically in the unmodified licensed snapshot and this integration. Those assertions were not removed or weakened. It also inventories the original host copy's missing test sources; the standalone host unit harness cannot compile as supplied upstream. Worker integration tests and the private whole-world checks cover the host paths used here. A full engine suite currently exits unsuccessfully because of the documented inherited failures.

`BUILD-VERIFICATION.json` describes the pre-summon baseline. `SUMMON-VERIFICATION.json` describes the later original throwdown/mount integration and its separately tested artifact. Neither report should be treated as evidence for an arbitrary future rebuild.
