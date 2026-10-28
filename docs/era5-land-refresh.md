# ERA5-Land refresh and development EDA

This manual acquisition workflow is retained for [research reproduction](setup.md). The project experiments are concluded; scheduled ingestion and a continuously updated prediction service are outside the [final scope](findings.md).

The refresh downloads discovered dates missing locally, including a first-time historical acquisition, or dates in an explicitly selected revision interval. Existing validated annual files are reused. Run the command explicitly; no scheduled update is configured. The expanded study covers 2020–2025, with training 2020–2024 and held-out test 2025. See [first-time setup](setup.md).

Install the local package once from the repository root:

```sh
.venv/bin/python -m pip install -e . --no-deps
```

Use the existing free Earth Engine configuration and project (authenticate through the provider's normal local setup). The existing project requirements supply `earthengine-api`, `requests`, `numpy`, and the notebook dependencies. Credentials and download URLs never enter manifests. Start with discovery; no data, checkpoints or manifests are written by `--dry-run`:

```sh
.venv/bin/python scripts/download_era5_land_daily.py --project YOUR_PROJECT --profile expanded --start 2020-01-01 --end 2025-12-31 --as-of 2026-10-25 --dry-run
```

Inspect `acquire_ranges`, `interior_gaps`, `unpublished_tail`, and `unavailable_other`. Only remove `--dry-run` when the plan is appropriate. A fixed cutoff bounds the requested UTC dates; discovery queries assets currently available, not a historical catalog snapshot. Catalog summary dates are not used to manufacture availability. A discovery response with duplicate, unordered or out-of-bounds days is rejected. Failed discovery acquires nothing. An entirely unavailable request reports `unavailable_other` rather than guessing whether it is delayed or permanently absent.

For a selected source revision, add an inclusive interval within the requested range:

```sh
.venv/bin/python scripts/download_era5_land_daily.py --project YOUR_PROJECT --start 2025-01-01 --end 2025-12-31 --as-of 2026-10-25 --revise 2025-09-01 2025-09-07
```

A revision uses fresh checkpoints. If acquisition fails, the error prints a `revision_id`; pass that same value with `--revision-id` and the same revision interval to resume. An ordinary refresh resumes validated incremental checkpoints automatically. A new revision without an ID deliberately fetches fresh source values again.

From `EDA/`, use `../.venv/bin/python ../scripts/download_era5_land_daily.py` with the same flags, or pass `--root /absolute/repository/path` from another directory. The package must be installed in the selected environment; running a script does not implicitly rewrite Python import paths.

## Committed inventory and failure recovery

`data/interim/era5_land_daily/manifest.json` is the commit pointer. Its cumulative `years` records remain compatible with the shared inventory and contain exact dates/ranges, checksums, source bands and units. The native NPZ keys remain `dates`, `latitude`, `longitude`, `temperature_2m_K`, and `soil_water_layer1_fraction`. Complete legacy annual files are validated and reused unchanged; extending a partial year preserves all earlier dates. Other years survive a bounded refresh unchanged.

Acquisition proceeds in at most seven-day contiguous groups. Each checkpoint is identified by source/version/bands/units/grid, exact dates and revision namespace. Its receipt stores checksum and retrieval time; shape, coordinates, dates, ranges and masks are revalidated on reuse. Mere file existence is insufficient. An unreceipted checkpoint is fetched again. Corrupt receipts/data stop with a diagnostic; inspect and remove only the affected checkpoint before retrying, rather than trusting damaged data.

New annual partitions are written, validated and promoted into `.partitions/` with content-addressed filenames. Only after all selected dates succeed does an atomic manifest replacement expose them. Earlier files are retained, and superseded paths are recorded in `retired_files`. An interruption may leave unreferenced files or valid checkpoints; the previous manifest and referenced source bytes remain usable. Do not load files by globbing all historical replacements. A `.refresh.lock` prevents concurrent writers; after an abrupt process termination, verify that no writer is active before removing a stale lock. Data and commit files must remain on the same local filesystem for atomic replacement.

Each committed request appends its study/cutoff/discovery and retrieval provenance in `runs`. Changed revision values append exact dates and old/new hashes in `revisions`; `downstream_invalidation` lists source hashes whose derived builds must be treated as stale by the dataset preparation workflow. The refresh does not automatically overwrite model tables. Unchanged repeats do not rewrite manifests or source files. A new request may extend coverage without claiming that the source is immutable.

## Validation and interpretation

Both fields are daily means of the source UTC day's hourly values. Temperature is stored in **kelvin**; only EDA presentation subtracts 273.15. Layer-1 water is a modeled volume fraction in the **0–7 cm** layer, not measured peat moisture or water-table depth. The configured native grid remains 0.1°, 96 × 111, EPSG:4326, north to south, without peat masking or 5 km resampling. Reconstructing a WITA day requires hourly source data; renaming these dates is invalid.

The loader checks checksums, chronological unique ISO dates, exact partition year/count, native coordinates, configured source identity, band presence/types/shapes, finite daily observations and physical units/ranges. NaN coastal masks are preserved. Every incoming day's finite mask must agree with the validated source mask; new holes, fully missing days, infinities, nonpositive kelvin values, or soil fractions outside [0,1] block the refresh or EDA. A changed mask needs scientific review, not silent imputation. Existing corrupt source files stop a refresh before fetching; restore a validated copy or deliberately establish corrected provenance first.

The ERA5 EDA uses the shared root resolver and runs from the repository root or `EDA/`. It validates inventory structurally, then only exposes dates returned by `Study.development_dates()`: expanded training 2020–2024 only. Held-out test 2025 does not enter default plots or distributions; 2026 is outside the study. Monthly means show actual valid-day counts and completion flags; annual quality tables mark partial years against 365/366-day calendars. Spatial maps use per-pixel valid-day denominators across observed development days. Missing source days remain gaps in daily lines. These province-level summaries are distinct from peatland-cell eligibility.

Run EDA in a clean project kernel after acquiring the training sources. A missing development archive raises a refresh instruction. The dated acquisition report records earlier verification; it is not a claim that a fresh clone contains those files.

The [Earth Engine ERA5-Land catalog](https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_LAND_DAILY_AGGR) documents hourly means for these non-flow bands, kelvin and layer-1 volume fraction. Its availability metadata and summary can differ; query actual assets. Publication delay and later reanalysis revisions mean this retrospective source is not an immediately available forecast input. Neither a source day nor retrieval time proves availability at a historical forecast issue cutoff. The study did not evaluate predictors available at issue time for a WITA next-day forecast; such forecasting is outside the completed scope. Band/depth/UTC choices are local reconstruction assumptions where Mahdiyasa et al. do not specify them.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_era5*.py' -v
```

Credential-free compact fixtures cover a fixed cutoff, delayed source, interior gap, leap day, atomic partial-year commit, repeats, resume after failure, extension across years, explicit revision/invalidation, legacy reuse, corrupt cache/source files, geometry/date/range/missingness rejection, and held-out-safe EDA denominators. They verify the refresh boundary without downloading real source data. These fixtures do not prove live provider availability. Inspect discovery and validate a small acquisition before a full first-time download.
