# Worker dependency notices

The engine's original project code is from licensed revision `4488651c35c44365faa1ba38eed5758b6ebde714`. Its exact grant and exclusions are in `UPSTREAM-README.md`, and its GPL-3.0-only text is in `LICENSE`. The Apache-2.0 mashup notices are preserved in `skate3-mashup/LICENSE` and `skate3-mashup/NOTICE`. Their statements about other applications, fonts, tools, or bundled assets retain their original scope; those items are not included in this worker tree.

## Selected Cargo dependencies

`DEPENDENCY-NOTICES.json` inventories the Windows x64 worker's selected normal and build dependency tree: 139 packages including five local crates, and 134 registry crates. It records each registry version, source, Cargo.lock checksum, declared license, selected alternative, and exact notice-file hashes. The corresponding texts are under `licenses/cargo/`.

The inventory comes from `cargo tree --locked --offline --edges normal,build --target x86_64-pc-windows-msvc`. Optional renderer dependencies present in the larger metadata graph are not represented as linked worker dependencies. All notice text was read from registry `.crate` archives whose SHA-256 matched the lockfile. The collection retains alternative license texts without selecting incompatible alternatives for the combined program.

## Bundled implementations

- Zstandard, including its bundled xxHash, FSE, and Huffman code, uses the BSD-3-Clause alternative. Its original GPLv2 alternative notice is also preserved, with no claim that it is the selected grant.
- BLAKE3 uses the Apache-2.0 alternative; its packaged C and assembly contain no additional license-bearing header comment blocks.
- The full libm notice text includes the musl-derived and other math implementation attributions. Exact additional copyright-bearing source comment blocks are retained separately.
- The miniz_oxide notices retain the original alternatives; Apache-2.0 is selected.

The manifest also identifies 75 exact copyright/license comment blocks extracted from the bundled native and math implementation files. These are notice text, not a copy of their implementation code. Each entry records its original source file and line.

## Rust standard library

`licenses/rust-1.96.0/COPYRIGHT-library.html` is the exact standard-library copyright document shipped with the compiler used for verification. The accompanying original license texts retain their stated scope, including target-specific dependencies that may not be part of this Windows executable. `TOOLCHAIN-NOTICES.json` records their hashes and compiler version. The compiler executable, standard-library object files, and toolchain sources are not included.

These files cover this worker source candidate and its selected build dependencies. Converter permissions, the loader, scripting runtime, and other parts of a complete mod package require their own notices and source inventories.
