# Dragonwilds collision exporter source

This snapshot builds the mod's exporter and its modified CUE4Parse dependency with .NET SDK **10.0.401**, targeting Windows x64 and the bundled .NET runtime **10.0.12**. NuGet dependencies are recorded in both projects' `packages.lock.json` files. Locked restore needs those package versions in a NuGet cache or network access to their public feeds.

Run `Build-Exporter.ps1 -Dotnet <path-to-dotnet.exe> -Output <new-output-directory>` from PowerShell. The script rejects an existing output directory. It does not read, launch, or modify a game.

Compilation explicitly disables incremental reuse before publishing, so copied source files with older timestamps cannot leave a stale exporter DLL in the output.

The `reference-cue4parse` subtree preserves its upstream license and notice. Its upstream revision and exact source inventory are recorded in `SOURCE-MANIFEST.json`. Local collision, transform, and cooked-geometry decoding changes are included as source.

All optional embedded resource payloads and native CMake build targets have been removed from this snapshot. This restricted build supports the Dragonwilds exporter; it is not a general replacement distribution of CUE4Parse. The exporter uses its managed decoder and does not download a proprietary native decoder.

The authored collision policy and Unreal mappings must be generated locally from an owned Dragonwilds installation. Neither game data nor prepared collision is included here. An omitted terrain component makes the exporter return a failure status, so partial exports cannot be published by setup.

This revision also exports `building-catalogue.json` from owned building data assets during private setup. The catalogue preserves authored entity transforms, meshes, tags, and preview body settings. It does not establish the live collision flags of player-built structures. Promoting that metadata into collision remains blocked until the live native state can be verified. Socketless component attachments now avoid an unnecessary identity-transform multiplication; named sockets retain their existing authored resolution.

The catalogue exporter retains parser warnings for unsupported health, damage, and snap native-component containers. Those containers are separate from the entity transform, mesh, body, and tag fragments used by the catalogue. An absent or unsupported entity representation remains explicitly unusable. A successful catalogue export means the selected metadata was read; it does not mean every native component was decoded or that player-built collision is ready.

Newly authored exporter code is included under the package's proposed GPL-3.0-only license; upstream dependency licenses and notices remain in effect. This is a development release candidate awaiting the author's final publication review.
