# Refresh FIRMS MODIS detections

Start with [first-time setup](setup.md). Use the project environment with the repository installed editable. Dates are inclusive UTC acquisition dates, never converted to WITA labels. MODIS Collection 6.1, confidence **strictly greater than 80**, and **all type codes** preserve the current research target. Existing `6.03` and `61.03` archive version strings remain visible; their C6.1 product identity is declared from the original export, never inferred from the version alone. No files in the old archive are rewritten.

## Free provider and archive fallback

Verified against official NASA documentation on 2026-10-25:

- [Area API](https://firms.modaps.eosdis.nasa.gov/api/area/) supports `MODIS_SP` and `MODIS_NRT`, explicit bounding boxes and inclusive start dates. Each request covers 1–5 days. A free MAP_KEY has 5,000 transactions per 10 minutes; large responses may consume multiple transactions. The fetcher sends sequential requests and aborts safely on provider errors; it does not promise unlimited backfills or bypass quotas. Retry in a new directory after quota recovery.
- [Availability API](https://firms.modaps.eosdis.nasa.gov/api/data_availability/) and [NASA's API tutorial](https://firms.modaps.eosdis.nasa.gov/content/academy/data_api/firms_api_use.html) document `data_id,min_date,max_date`. The fetcher checks these at runtime and rejects requests outside the returned range with archive instructions. An old date is not assumed supported just because a URL can be constructed.
- [Archive download](https://firms.modaps.eosdis.nasa.gov/download/) offers MODIS Collection 6.1 exports through Earthdata or email authentication. NRT is replaced with standard science-quality data after approximately five months. Save the export confirmation with requested dates and geographic extent. Mixed-stage exports must be split into independently declared SP/NRT imports using row version and retained evidence.

Configure `FIRMS_MAP_KEY` through your environment; never commit it or include it in a ledger. For a current acquisition use a current explicit UTC cutoff timestamp. The fetcher rejects actual retrieval times later than the cutoff, so it cannot backdate provider evidence.

```sh
python scripts/fetch_firms.py --profile expanded \
  --start 2025-01-01 --end 2025-01-10 \
  --as-of 2026-10-25T23:59:59Z --bbox 108.5 -5 119.5 4.5 \
  --stage SP --output data/raw/Firms/refresh-2025-snapshot
```

Use the real current UTC cutoff when executing; the example is a documented historical snapshot, not an instruction to fabricate retrieval time. If availability rejects the interval, request **MODIS Collection 6.1 CSV** from the archive page using the same start/end and rectangle. Keep the confirmation and prepare the ledger below. The provider path does not send email or automate authentication. Fetching requires network access and a user-provided free key; tests require neither.

## Interval ledger

The normalized path is shared by provider responses and manual exports. A JSON ledger is versioned alongside its immutable CSVs. Source paths resolve relative to the ledger. Calculate each file's actual SHA-256; do not copy the placeholder. The declared rectangle must contain the entire candidate mask to authorize negatives. Narrower valid imports can contribute positive observations but do not establish study-wide absence coverage.

```json
{
  "schema_version": 1,
  "intervals": [
    {
      "start": "2025-01-01",
      "end": "2025-01-10",
      "bbox": [108.5, -5, 119.5, 4.5],
      "product": "FIRMS_MODIS_C6.1",
      "stage": "SP",
      "status": "complete",
      "path": "fire_archive_export.csv",
      "sha256": "REPLACE_WITH_ACTUAL_SHA256",
      "retrieved_at": "2026-10-25T12:00:00Z",
      "provenance": "Export confirmation ID and saved request with inclusive dates, bbox and collection"
    }
  ]
}
```

`complete` means a successful export/query with the declared interval and bounds, including a header-only response. The end UTC day must have closed before retrieval for a complete interval. A missing source, failed query, absent confirmation or malformed row cannot establish completeness. Use `unknown` for unverified archive coverage; `failed` intervals may omit a source path. Files that are provided require a checksum. Acquisition date/time, sensor, satellite, confidence, version, extent and stage are validated. Any rejected row downgrades that import's completeness. The original record remains in normalized output, including optional source pixel fields. Required MODIS columns are latitude, longitude, acquisition date/time, satellite, instrument, confidence, and version; absent API `type` remains unknown rather than fabricated. The original type policy retains all records.

The provider fetcher writes a new directory only after all responses validate. It never overwrites old exports or a good build. A query that includes the still-open UTC retrieval day is marked `unknown` even if the response succeeds. Full retrieval does not imply cloud-free observation or verified absence of fire. Multiple imports append as additional ledger entries rather than replacing prior years. Failed attempts leave no published success ledger. Existing ledgers/source directories remain available across invocations.

## Deduplication and corrections

Identity is exact acquisition timestamp, satellite, instrument, declared product, latitude and longitude. Different sensors/overpasses survive; there is no approximate spatial merge. Original source fields, checksums, row number and provenance remain auditable. For duplicate identities, newest retrieved record wins with checksum/row tie breaks; all import provenance and changed-record-group counts are retained.

A **complete, valid SP interval** supersedes NRT detections in the same declared rectangle and dates, including detections deleted or moved by reprocessing. This avoids retaining stale NRT positives absent from corrected SP. A malformed/unknown SP import cannot erase NRT evidence. A later retrieved complete SP interval also supersedes older SP records within its dates and rectangle, including removed or moved detections. Equal-retrieval-time imports have no inferred revision order; conservative identity deduplication applies. NRT and SP replacement counts are separate from duplicate counts. SP replacement is interval based because coordinates can change; it does not claim a pixel correspondence that the export does not provide. If replacement of only one satellite is intended, this combined MODIS ledger contract is insufficient: obtain the complete combined MODIS export instead.

## Build labels and development EDA

The candidate grid must already exist from spatial preprocessing. `load_spatial` reads its stable addresses and uses the exact projected polygon/intersection and covers/floor policies; it does not regenerate geometry or change the BIG mask. No weather or spatial-feature preparation is run.

```sh
python scripts/import_firms.py --profile expanded --as-of 2026-10-25 \
  --ledger data/raw/Firms/refresh-2025-snapshot/intervals.json
```

The command prints a new immutable `data/interim/builds/<profile>/<study-id>/firms/<firms-build-id>/` containing normalized and assigned detections, daily labels, the development coverage/detection plot and manifest with source/output hashes. It fails if the exact build already exists; reuse that build. Sources are never modified. Build identity includes the study, complete interval evidence, lattice and boundary hashes. A failed build cleans up its temporary output and preserves older committed builds.

The default writes **development dates only** (expanded: 2020–2024; legacy: 2020–2023). `--include-held-out` explicitly enables test-period label exports; the plot remains development-only. `reported_detection_count` counts available positives, including incomplete intervals. `detection_count` is nullable until whole-mask retrieval is complete. `fire_detected` is 1 for an observed qualifying positive, 0 only for complete retrieval without a positive, and blank otherwise. `retrieval_coverage` distinguishes `complete_sp`, provisional `complete_nrt`, and `unknown`. No label implies a ground-truth fire-absence observation.

Set `FIRMS_LEDGER`, optionally `PEATGUARD_PROFILE`, `PEATGUARD_AS_OF`, and `PEATGUARD_END`, then run `EDA/02_FIRMS_EDA.ipynb` from root or `EDA/`. Without a ledger it previews discovered legacy archives with unknown coverage and explains the missing evidence. It never infers dates from the filename or zero-fills missing months. The temporal plot shows requested development coverage, including unknown days and partial years; outcome distributions exclude held-out dates. No EDA data files are written.

## Reproduce verification

```sh
python -m unittest discover -s tests -p 'test_firms*.py' -v
```

Fixtures cover a complete local source-to-labels-and-plot build, fixed cutoff, read-only sources, confidence 80, type retention, overlap, distinct satellites/overpasses, changed records, authoritative SP replacement, successful empty intervals, failed/unknown/malformed intervals, bbox guards, source checksum failure, original UTC times across the WITA day boundary, 1–5-day provider requests, archive fallback, error responses, and held-out exclusion. Provider responses are injected official-schema fixtures; no live acquisition or credential authentication is claimed.

## Public downloads verified during ticket #8

NASA's official [country/year listing](https://firms.modaps.eosdis.nasa.gov/data/country/yearly_summary_files.txt), linked through its [country download page](https://firms.modaps.eosdis.nasa.gov/country/), supplies a public 2025 Indonesia point CSV without a MAP_KEY:

`https://firms.modaps.eosdis.nasa.gov/data/country/modis/2025/modis_2025_Indonesia.csv`

A public recent NRT snapshot also works:

`https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_SouthEast_Asia_7d.csv`

Both were acquired on 2026-10-25, stored immutably in the local refresh folder, checksummed and normalized. The public NRT header omits `instrument` and `type`. An interval can explicitly declare `declared_instrument: "MODIS"` from this named provider endpoint; absent type stays unknown and the original record is preserved. An arbitrary missing instrument is still rejected. The raw inventory accepts that omission only when the declared sensor is tied to the exact CSV checksum.

These downloads are **not rectangular query/export coverage evidence**: the country export's geographic boundary has not been verified against the exact BIG mask, and a rolling seven-day snapshot does not declare complete UTC-day retrieval. Their ledger status is `unknown`, so valid positive detections are retained but unsupported negatives remain missing. Full 2026 acquisition still needs configured API access or an authenticated archive export; it cannot be inferred from this recent snapshot. The existing archive also lacks original request dates/bounds confirmation.

`EDA/02_FIRMS_EDA.ipynb` now uses local `data/raw/Firms/intervals.json` by default, with `FIRMS_LEDGER` as an explicit override. The acquired 2025 file is registered as the test-year source. Default EDA summarizes training years 2020–2024 only; 2026 intervals are outside the study and skipped during ledger loading. The dated ledger records validation envelopes separately from the provenance notes explaining unknown coverage. No credentials appear in it.

Current research split: train 2020–2024, held-out test 2025. The historical acquisition notes above retain the earlier 2026 backfill context; 2026 is not part of the active research profile. Default preparation creates the frozen 2025 test partition separately, and default loading keeps it closed.
