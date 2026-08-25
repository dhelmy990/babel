# Rolling Dataset Release Contracts

## Goal

Make the fixed `distillation_2016` publication bundle self-describing,
strictly validated, append-only, remotely loadable, and incapable of claiming a
complete release without independently bound reconciliation evidence.

## Public contracts

Two install-safe JSON Schemas define the new inputs:

- `dataset-manifest-v1` closes the manifest and shard shapes. It fixes the
  manifest, row-schema, and configuration versions; validates split and
  aggregate counts; requires canonical pilot keys and row digests; and requires
  every shard's path, split, size, checksum, row count, rank/key bounds, and
  identity digest.
- `full-release-proof-v1` closes the proof shape. It binds the canonical
  provenance digest, accepted JSONL artifact and row count, raw/accepted/
  excluded reconciliation counts, a `complete` reconciliation marker, and the
  complete source inventory identities.

Canonical schemas under `schemas/` and packaged copies under
`data_pipeline/src/babel_data/schemas/` remain byte-identical and are exercised
from an installed wheel.

## Central validation

`babel_data.release` owns canonical JSON, manifest parsing and validation,
rolling-extension validation, deterministic dataset-card rendering, readiness
alignment, and full-release-proof validation. Sharding constructs a manifest
through this contract; publication validates the same contract for both local
and pinned remote bytes before using any field.

Each shard's `rows_sha256` hashes the canonical ordered sequence of
`[article_key, page_id]` identities. Within one manifest, shard paths, Parquet
SHA-256 values, and identity digests are unique. Rank intervals are ordered and
disjoint within each split. A rolling extension preserves the complete prior
shard prefix and all prior interval/key/digest evidence, and every appended
rank interval follows the prior interval for its split. These rules reject
copied blobs under aliases and every identity overlap provable from the
manifest.

## Publication bundle

Every publication contains, in one compare-and-swap commit:

- every manifest-listed Parquet shard;
- `distillation_2016/manifest.json`;
- root `readiness.json`; and
- root `README.md`.

The deterministic README dataset-card YAML declares exactly one
`distillation_2016` configuration and train/validation/test Parquet globs.
Because the globs cover future parts, the README is immutable after its first
publication.

The uploaded readiness document is the exact pre-publication local document.
It need not claim its own not-yet-known commit SHA. Its schema, example count,
shard identities, and accepted-input checksum must align with the manifest.
Remote verification fetches manifest, readiness, and README at the returned
immutable commit, compares exact bytes, validates their contracts, then streams
and semantically validates representative rows. Local post-commit evidence and
the accepted commit SHA remain separate atomic files used by deletion safety.

## Complete-release gate

`publish-2016 --state complete` requires `--full-release-proof PATH`. The CLI
validates the proof before token lookup or API construction. The proof is
accepted only when:

- its canonical provenance SHA matches the manifest provenance document;
- its accepted JSONL checksum, size, and row count match provenance and the
  full manifest count;
- `raw = accepted + excluded`, and those counts match the complete
  reconciliation report;
- reconciliation is explicitly marked complete; and
- its source inventory is an exact identity-preserving copy of provenance
  sources, with record counts supplied by the proof.

Missing, malformed, incomplete, or pilot-sized proof fails before any remote
operation or output handoff. `pilot_ready` remains the ordinary Task 5 path.

## Error handling and verification

All contract failures are fail-closed and precede remote writes. Existing
bounded retry, private-repository proof, pinned-parent preflight, no-clobber
directory publication, and durable deletion evidence remain intact.

Tests cover closed schemas and malformed fields, aggregate/count/digest
inconsistency, copied and metadata-altered shard aliases, readiness/card upload
and exact pinned verification, dataset-card path coverage, and complete-release
proof rejection before API access. Final verification runs focused data tests,
all data/training tests, and copied-source offline wheel plus console-entrypoint
checks without remote access.
