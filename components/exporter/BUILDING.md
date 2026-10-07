# Dragonwilds collision exporter source

This snapshot builds the mod's exporter and its modified CUE4Parse dependency with .NET SDK **10.0.401**, targeting Windows x64 and the bundled .NET runtime **10.0.12**. NuGet dependencies are recorded in both projects' `packages.lock.json` files. Locked restore needs those package versions in a NuGet cache or network access to their public feeds.

Run `Build-Exporter.ps1 -Dotnet <path-to-dotnet.exe> -Output <new-output-directory>` from PowerShell. The script rejects an existing output directory. It does not read, launch, or modify a game.

The `reference-cue4parse` subtree preserves its upstream license and notice. Its upstream revision and exact source inventory are recorded in `SOURCE-MANIFEST.json`. Local collision, transform, and cooked-geometry decoding changes are included as source.

All optional embedded resource payloads and native CMake build targets have been removed from this snapshot. This restricted build supports the Dragonwilds exporter; it is not a general replacement distribution of CUE4Parse. The exporter uses its managed decoder and does not download a proprietary native decoder.

The authored collision policy and Unreal mappings must be generated locally from an owned Dragonwilds installation. Neither game data nor prepared collision is included here. An omitted terrain component makes the exporter return a failure status, so partial exports cannot be published by setup.

Newly authored exporter code is included under the package's proposed GPL-3.0-only license; upstream dependency licenses and notices remain in effect. This is a development release candidate awaiting the author's final publication review.
