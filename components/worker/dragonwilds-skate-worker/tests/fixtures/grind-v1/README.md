# Native graph format fixtures

The `legacy-*` files were exported by the original Vec-based graph module before compact storage changes. Each synthetic binary is stored as UTF-8 `.hex` text and decoded in the test's temporary directory. Their explicit test algorithm pin is 64 hexadecimal `f` characters. They cover branches, signed zero, shared cell references, short components, singleton rails, edge removal and full compaction.

Cold base, endpoint delta and additions-only full compaction must retain exact bytes. The original post-deletion compact file retains a valid randomized slot layout; tests compare its public rail semantics, while checking exact load/store roundtrips of current snapshots. Rejected updates can also leave a different valid internal slot layout. Public geometry, census, owners and future retired-owner deltas remain the required contract.

The `c...` and `d...` files and `records.json` came from the real worker fixture exporter, including its initial-provider owner-rebind boundary. Python independently verified their complete record and payload hashes.
