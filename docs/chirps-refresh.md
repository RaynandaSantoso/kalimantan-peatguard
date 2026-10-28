# CHIRPS v2 Final: bounded local refresh

This manually invoked workflow is retained to reproduce bounded research inputs. The [completed project](findings.md) does not operate or plan a scheduled ingestion or continuous prediction service.

This path preserves the configured native 0.05° geographic grid and mm/day
precipitation. It is a source-grid subset, without peat masking or 5 km resampling.
Dates are source-day UTC, and neither rainfall nor current asset metadata proves
that an observation was available for a historical forecast issuance.

Install the repository in its existing environment (`python -m pip install -e .
--no-deps`). Acquisition additionally needs the existing `earthengine-api` and
`requests` dependencies, free Earth Engine access, a registered eligible project,
and normal local authentication. Authenticate through Earth Engine's own tools;
never paste credentials into scripts, manifests, or notebooks. No authentication
is required for the fixture tests or validated local EDA.

From the repository root, use its virtual environment. The script also works from
`EDA/` as `../scripts/download_chirps_v2.py`; pass `--root /path/to/repository` when
running outside the repository.

```sh
# Validate local data, discover exactly this interval, and inspect gaps first.
.venv/bin/python scripts/download_chirps_v2.py --project PROJECT_ID --profile expanded --start 2025-01-01 --end 2025-12-31 --as-of 2026-10-25 --discover-only

# Bounded backfill; only actual discovered image dates are requested.
.venv/bin/python scripts/download_chirps_v2.py --project PROJECT_ID --profile expanded --start 2025-01-01 --end 2025-12-31 --as-of 2026-10-25

# First-time acquisition of the full active study (2020–2025).
.venv/bin/python scripts/download_chirps_v2.py --project PROJECT_ID --profile expanded --start 2020-01-01 --end 2025-12-31 --as-of 2026-10-25

# Force a bounded provider revision check while retaining all other dates.
.venv/bin/python scripts/download_chirps_v2.py --project PROJECT_ID --profile expanded --start 2025-01-01 --end 2025-12-31 --as-of 2026-10-25 --refresh-start 2025-09-01 --refresh-end 2025-09-30

# Convenience full-year backfill within the configured profile.
.venv/bin/python scripts/download_chirps_v2.py --project PROJECT_ID --years 2020 2021 2022 2023 2024 2025 --as-of 2026-10-25
```

`--years` requires a contiguous interval and cannot be combined with explicit
start/end. The profile controls valid bounds and split roles. All inclusive user
intervals become exclusive-end provider queries. Future explicit dates fail.
Discovery is bounded to the requested interval and runs before downloads, so an
unpublished remainder of the year is never requested as image bands. JSON output
separates published dates, validated dates, published-but-missing dates, interior
source gaps and an unpublished tail. When no asset is returned, coverage is
unknown/outside coverage rather than a falsely established publication tail.
A gap means the requested complete period is incomplete; the adapter records and
can ingest available dates without certifying a complete modeling partition.
Source integrity is distinct from downstream model eligibility.

The cumulative `manifest.json` remains the active inventory. Complete legacy
annual arrays remain readable and are validated by checksum, dates, bands, native
coordinates, dimensions and rainfall validity before reuse. A partial annual
entry lists its actual dates, image identities and per-date retrieval provenance.
Legacy retrieval timestamps remain unknown when the original manifest lacked them.
An adapter/configured product or grid change fails instead of mixing experiments.

New annual files are immutable content-addressed arrays under `.partitions/`.
All candidate files are validated and persisted before an atomic manifest swap.
A failure leaves the previous inventory and all its referenced arrays untouched;
unreferenced candidates are harmless. Old versions remain preserved. The manifest
lists superseded paths under `retired_files`, so inventory consumers must ignore
those paths when globbing while retaining all active `years` records. Concurrent
writers are prevented by an advisory lock.

Up to seven published days are cached per chunk under `.checkpoints/`, keyed by
source, version, grid, exact dates and refresh policy. Rerun the **same interval,
cutoff and chunk policy** after failure to resume without repeating validated
chunks. Corrupt/incompatible archives or checkpoints fail explicitly. Restore a
corrupt committed archive from its known-good version; after checking diagnostics,
remove only the corrupt checkpoint's JSON reference to re-request its chunk.
Do not delete committed inventory as a shortcut. Checkpoint data and retired annual
versions are retained for recovery; manual cleanup must preserve all active paths.

Ordinary duplicate runs discover availability and reuse validated content. A
successful revision clears its checkpoint references so the next explicit refresh
checks the provider anew. Changed days and old/new hashes are recorded, along with
`downstream_invalidations` entries identifying consumers of replaced upstream
hashes. Downstream builders must compare their recorded upstream hashes against
this ledger and refuse reuse/rebuild affected artifacts. The chronological dataset builder checks upstream source provenance; see
[dataset preparation](dataset-preparation.md).

`EDA/03_CHIRPS_EDA.ipynb` defaults to configured development dates (expanded:
2020–2024 training only; 2025 is held-out test). It validates source integrity before
loading, reports actual date coverage, and does not expose held-out rainfall
summaries unless `include_held_out=True` is deliberately selected. That flag is
returned as `held_out_opened` metadata; it does not freeze the final test interval.
Charts show valid-day denominators and hatch partial months. Missing periods do
not become zeros. Monthly climatology excludes incomplete months. Mean annual
maps include only complete calendar years and propagate missing pixel-days.
Province-level descriptions do not represent peatland model coverage.

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_chirps*.py' -v
```

Tests use compact deterministic provider fixtures and a fixed cutoff, including
legacy import, leap day/year transitions, partial-period extension, publication
gaps, interruption/resume, revisions, cache corruption, incompatible grids and
failure before manifest promotion. They perform no network requests or credential
reads. Fixtures do not establish live provider coverage. See the dated acquisition
report for historical download evidence; inspect discovery on each new run. CHIRPS v3 migration remains a separate
experiment and ticket #7; v3 is never spliced into these v2 files.
