# Adapter source candidate

The source in this archive exactly matches the package adapter. Newly authored
adapter code is released under GPL-3.0-only. Retained upstream code keeps its
original licenses and notices. Gameplay acceptance and publication are separate.

Python runtime modules are under app; UE4SS scripts are under host. Native helper
sources are under helpers. Build helpers with Build-Helpers.ps1 using Rust 1.96.0
and the Windows MSVC toolchain. Cargo.lock files pin dependencies. Their exact
license texts are in the package notices/helpers directories. Cargo downloads
dependencies from the registry; no game assets or converters are included.

The worker and managed exporter have separate corresponding-source archives and
build instructions. The private package recipe is deliberately omitted: supply
local input locations and their reviewed SHA256 values to release-tools/build_release.py.
The builder only stages a folder. It does not create or publish a release ZIP.
