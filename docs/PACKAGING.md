# Producing an installable release

The source tree contains the audited allowlist package builder at
`components/adapter/release-tools/build_release.py`. Its schema is documented in
`components/adapter/release-tools/BUILDER-CONTRACT.md`; installer behavior is in
the adjacent `INSTALLER-CONTRACT.md`.

## Inputs still required locally

1. Tested worker executable plus a newly inventoried corresponding-source ZIP,
   exact compiled-source fingerprint, verification record and binary SHA256.
2. Built IPC/render/building helpers, their source fingerprints and retained notices.
3. Each separately built exporter, a designated byte inventory and corresponding
   source archive. Keep the base exporter and supplementary exporter distinct.
4. Official embedded Python, NumPy and Pillow downloads matching the provenance
   records, a prepared runtime tree, and a reviewed SDL3 DLL.
5. Separately obtained exact UE4SS prerequisite files. They are checked locally
   but excluded in external-loader distribution mode.
6. Compatibility hashes and the reviewed building rule for the tested game build.
7. A local schema-1 recipe selecting those exact files, artwork, documentation,
   wizard scripts and their SHA256 values. Keep it outside tracked source or in
   the ignored `local/` directory. Never commit paths to a personal installation.

The existing component build scripts compile source; they do not automatically
mint these release designations. A package cannot honestly claim a previously
verified binary identity after its source or toolchain changes. Final source ZIP
and helper/exporter designations must be regenerated and audited from final inputs.

```powershell
python -B tools/dev.py package --recipe local/release.private.json --output build/stage
```

This delegates to the existing builder and re-runs its package audit. It creates
a new staged directory only; it does not ZIP, upload, commit, push or install.
The final release still needs the recorded synthetic/cache tests and gameplay
acceptance. No GitHub Actions workflow is allowed to use private game data.

