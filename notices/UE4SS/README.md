# UE4SS release notice audit

## Packaging decision

Keep the exact official loader as a **manual prerequisite** in the current draft. Do not include `UE4SS.dll` or `dwmapi.dll` on the strength of the root MIT notice alone. `UEHelpers.lua` is public first-party source and can retain that MIT notice.

The exact compiled UE4SS source is `e3ba1016562d6c0868c410d0a71e88bfcdbf691b`. Its private Unreal submodule is `38e7171e9e8c4a871ff84765848f0290ca34a44e`. The [pinned official contributing guide](https://github.com/UE4SS-RE/RE-UE4SS/blob/e3ba1016562d6c0868c410d0a71e88bfcdbf691b/docs/contributing.md#uepseudo-code-licensing) explicitly places UEPseudo under Epic Games' licensing terms. Those compiled redistribution terms have not been established here. No access restriction was bypassed.

## Obtained material

`notices/` contains 177 text files (844,632 bytes), with source URLs and SHA256 values in `notice-manifest.json`. This is an obtainable notice inventory, **not a claim of complete redistribution permission**. The original release ZIP has only `ue4ss/LICENSE`.

Collected material covers the main MIT notice, Shade's USMap notice, Lua 5.4.7, GLFW, GLAD/Khronos, ImGui and stb, ImGuiColorTextEdit, Glaze and embedded conversion/hash code, PolyHook, asmjit/asmtk, Zydis/Zycore, raw_pdb, fmt, PatternSleuth, fonts, and all 73 registry packages in the pinned Rust lockfile. All 73 crate archives passed their lockfile checksums. Optional Tracy notices are marked as inventory; the official build defaults to no profiler.

Embedded font metadata identifies Roboto Regular 2.137 (Apache-2.0), Roboto Mono 3.001 (OFL), and Font Awesome Free Solid 5.15.4 (OFL). Upstream's adjacent Roboto notice labels the regular font OFL; both the supplied notice and actual embedded attribution have been preserved. Font Awesome's font license applies here, rather than its SVG icon license.

Additional inventory limits are recorded in `audit.json`: IconFontCppHeaders uses moving `main`; the lockfile is a conservative dependency inventory, not an exact linked Windows SBOM; Rust's complete standard-library third-party copyright report was not independently closed. These do not need resolution to ship an adapter that requires the official loader separately.

## Our native helpers

`skate-ipc` has no Cargo dependency beyond Rust's standard library and imports Windows kernel32 APIs. It resolves two Lua API entry points from the installed loader at runtime. `skate-render` uses glam 0.32.1 and Rust's standard library. Neither inspected helper imports or statically links UE4SS/UEPseudo headers or libraries. Their manifests currently omit a source license field; apply the package's explicit license and retain glam and runtime notices. The exact glam archive was checksum-verified and its notices are included separately.

## Reproducibility

- `audit.json`: decision, artifact hashes, evidence and unresolved scope.
- `notice-manifest.json`: every collected text's source URL and hash.
- `source-provenance.json`, `source-tree.json`: exact official source archive and submodule pins.
- `dependencies-provenance.json`, `cargo-notice-inventory.json`, `supplemental-provenance.json`: downloaded source/notice provenance.
- `build_notice_inventory.py`: deterministic collection from saved source material.

No game input, installed file changes, external messages, or uploads were performed.
