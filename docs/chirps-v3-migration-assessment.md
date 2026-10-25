# CHIRPS v3 migration assessment

Access date: **2026-10-25** (Asia/Jakarta). Ticket #7. This assessment distinguishes provider documentation and directory metadata from local statistical evidence. It does not change the configured research rainfall source or examine held-out 2025 outcomes.

## Decision

**Keep CHIRPS v2 Final as the existing research baseline while assessing a deliberate, consistent rebuild with one named CHIRPS v3 daily variant.** This is a continuity decision, not a finding that v2 is more accurate. Do not append v3 observations to a v2 training series or silently substitute preliminary for final values.

CHC says v2 production ends after December 2026; it is not announcing that the rainfall record disappears. CHC encourages transition to v3. Historical v3 data already overlap this project's development years, so migration need not introduce a version boundary between training and test. [CHC v2 notice](https://www.chc.ucsb.edu/data/chirps)

## What changes scientifically

V3 changes the climatology, satellite estimation algorithm, station inputs, and gauge-undercatch treatment. These are changes to the measurements used by the model, not just a download address. The authors report improved mean and variance representation in their evaluated regions; that is not independent proof of better Kalimantan daily fire prediction. [Funk et al. (2026), Scientific Data](https://www.nature.com/articles/s41597-026-07096-4)

The provider notes that v3 is generally wetter than v2 after gauge-undercatch correction. The nominal global land grid is 0.05°, extending from 60°S to 60°N across all longitudes; v2 covers 50°S–50°N. Both cover Kalimantan. Preserve the project's existing analysis grid and explicitly validate raster alignment instead of assuming equal resolution guarantees identical exported arrays. [V3 archive README](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/README-CHIRPSv3.0.txt), [v2 Earth Engine catalog](https://developers.google.com/earth-engine/datasets/catalog/UCSB-CHG_CHIRPS_DAILY)

## Daily variants and time meaning

CHIRPS is constructed at pentadal/monthly steps, then disaggregated daily. V3 **rnl** uses ERA5 daily proportions (1981 onward); **sat** uses IMERG Late V07 (1998 onward). They share pentad totals but can allocate rain to different days. Neither is an independent daily station analysis. Zero disaggregator rain can trigger equal allocation and artificial wet-day runs. CHC gives no universal variant preference and recommends sensitivity testing when daily timing matters. Current preliminary daily v3 uses sat only. [Official daily README](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/readme.txt)

The same README identifies date D with UTC disaggregation approximately 00:00 D–00:00 D+1. Station windows underlying the pentad are heterogeneous, so this is not a uniform station-observation window. Converting labels to WITA does not reconstruct a WITA daily accumulation. Rnl/ERA5 agreement and sat/IMERG agreement are partly built in and cannot independently validate rainfall accuracy. [Official daily README](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/readme.txt)

For comparison, v2 daily uses CFSv2 temporal disaggregation, also with UTC labels and heterogeneous station windows. Its historical same-day daily values do not establish availability at an earlier forecast issue time. [Official v2 daily README](https://data.chc.ucsb.edu/products/CHIRPS-2.0/README-CHIRPS2-Daily.txt)

## Publication timing and live forecasting

Preliminary v3 is produced two days after each pentad ends; final production is monthly, typically during the third week of the following month. These are expected production schedules, not guaranteed per-file publication timestamps. Preliminary observations should eventually be replaced with final values while preserving revision provenance. [CHC v3 overview](https://www.chc.ucsb.edu/data/chirps3), [archive README](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/README-CHIRPSv3.0.txt)

**Inference for PeatGuard:** neither product supplies tomorrow's observed rainfall. Even preliminary data can be several days behind the issue date. An operational model needs lagged inputs actually available at issue time, or a separately evaluated weather-forecast source with archived issue times. Training on later final observations and serving preliminary or lagged observations changes the input distribution. Migration to v3 alone cannot solve that issue.

## Free access and exact identities

Public HTTPS directory listings were accessible without credentials. Example daily GeoTIFF patterns, confirmed from provider directory listings:

| Product | URL pattern |
| --- | --- |
| V3 final rnl | `https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/rnl/YYYY/chirps-v3.0.rnl.YYYY.MM.DD.tif` |
| V3 final sat | `https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/sat/YYYY/chirps-v3.0.sat.YYYY.MM.DD.tif` |
| V3 preliminary sat | `https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/prelim/sat/YYYY/chirps-v3.0.prelim.YYYY.MM.DD.tif` |

Sources: [2025 rnl listing](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/rnl/2025/), [2020 sat listing](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/sat/2020/), [preliminary sat listing](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/prelim/sat/2026/). The preliminary filename omits `sat`; its directory establishes the variant.

| Earth Engine product | Collection | Band and units |
| --- | --- | --- |
| V2 final daily | `UCSB-CHG/CHIRPS/DAILY` | `precipitation`, mm/day |
| V3 daily rnl | `UCSB-CHC/CHIRPS/V3/DAILY_RNL` | `precipitation`, mm/day |
| V3 daily sat | `UCSB-CHC/CHIRPS/V3/DAILY_SAT` | `precipitation`, mm/day |

Official catalogs: [v2](https://developers.google.com/earth-engine/datasets/catalog/UCSB-CHG_CHIRPS_DAILY), [v3 rnl](https://developers.google.com/earth-engine/datasets/catalog/UCSB-CHC_CHIRPS_V3_DAILY_RNL), [v3 sat](https://developers.google.com/earth-engine/datasets/catalog/UCSB-CHC_CHIRPS_V3_DAILY_SAT). Catalogs identify public-domain data and free research/education/nonprofit Earth Engine access requiring registration. Public files offer an alternative acquisition route; the implemented comparison below uses the existing registered Earth Engine project, with no paid export or hosted service. These facts do not promise unlimited hosted application use.

## Availability snapshot, not held-out evaluation

On the access date, both v3 final 2026 directory listings contained 273 daily files, ending **2026-09-30**; preliminary sat ended **2026-10-20**. The Earth Engine v2/rnl/sat catalog availability fields also ended September 30. These are metadata checks only: no 2026 rainfall values or fire outcomes were inspected. Current listings do not prove when each historical file became available. [Final rnl listing](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/rnl/2026/), [final sat listing](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/sat/2026/), [preliminary listing](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/prelim/sat/2026/)

## Bounded development-period comparison

The comparison **ran successfully on 2026-10-25**, using the existing Earth Engine project `kalimantan-peat`. The interval was fixed before examining differences: **2024-01-01 through 2024-02-29 inclusive (60 days)**, within expanded training. No 2026 rainfall values or target outcomes were loaded. Both candidate collections supplied all 60 requested dates; there was no external-access block.

| Item | Observed scope |
| --- | --- |
| Reference | Existing locally validated CHIRPS v2 Final, `UCSB-CHG/CHIRPS/DAILY`, version `2.0_final` |
| Candidates | `UCSB-CHC/CHIRPS/V3/DAILY_RNL` and `.../DAILY_SAT`, historical v3 daily collections |
| Units | mm/day for all products |
| Target grid | EPSG:4326, 190 rows × 220 columns, affine `[0.05, 0, 108.5, 0, -0.05, 4.5]` |
| Bounds | 108.5–119.5°E, 5°S–4.5°N |
| Mask | Native target pixel centers inside the five BIG provinces; 17,382 pixels; not a peat-cell eligibility mask |
| Requested pixel-days | 1,042,920 per comparison |
| Valid v2 / valid candidate | 1,042,860 / 1,042,860 for each variant |
| Matched valid pairs | 1,042,800 for each variant |
| Unmatched coverage | 60 v2-only and 60 candidate-only pixel-days; no both-missing pairs |

Statistics below use **only matched valid pixel-days**. The 120 unmatched observations are reported rather than zero-filled. Means are unweighted over native pixels and days; no area weighting, significance test or claim of independent samples is made.

| Metric | V2 reference | V3 rnl | V3 sat |
| --- | ---: | ---: | ---: |
| Mean rainfall, mm/day | 10.187 | 10.108 | 10.108 |
| Median, mm/day | 7.915 | 7.272 | 5.533 |
| 95th percentile, mm/day | 31.003 | 29.701 | 34.964 |
| 99th percentile, mm/day | 43.851 | 45.508 | 54.414 |
| Fraction ≥1 mm/day | 63.73% | 86.18% | 75.95% |
| Mean candidate minus v2, mm/day | — | −0.079 | −0.079 |
| Mean absolute difference from v2, mm/day | — | 7.112 | 6.129 |
| Root mean squared difference from v2, mm/day | — | 10.434 | 9.476 |
| Pixel-day Pearson correlation with v2 | — | 0.522 | 0.678 |
| Disagreement at the ≥1 mm threshold | — | 25.95% | 19.09% |

**Interpretation:** the two-month total differs from v2 by only about −0.78%, but the daily allocation differs substantially. Similar accumulated amounts do not establish interchangeable daily inputs. Sat is closer to v2 by these daily agreement measures in this sample; v2 is not ground truth, so this is not evidence that sat is more accurate. Both v3 variants have effectively identical accumulated amounts over the complete pentads/months here, consistent with their documented common pentad constraint. The reported correlation is pooled over spatially and temporally dependent pixels; it is not an independent validation statistic.

This is a bounded two-month sample, not a multi-season Kalimantan evaluation. It does not test drought spells across years, local gauges, peat-only features, fire prediction, or issue-time availability. Retain rnl as a sensitivity comparator. For a future migration experiment, **final sat is a provisional first candidate** because of its closer daily agreement here and the provider's use of sat in preliminary production; neither observation authorizes switching or proves that training on final values is safe for serving preliminary data. Extend development-only seasonal/gauge checks before committing to a variant.

### Alignment actually observed

Authenticated metadata checks found native EPSG:4326 transforms `[0.05, 0, -180, 0, -0.05, 50]` for v2 and `[0.05000000074505806, 0, -180, 0, -0.05000000074505806, 60]` for both v3 variants. V3 images report 7,200 × 2,400 native dimensions. First/last compared candidate projections matched. V3's float spacing creates a maximum observed corner registration offset of roughly **0.0000893 source pixel** on this subset. The comparison explicitly requests the existing v2 subset grid with Earth Engine's default nearest-neighbor sampling, rather than claiming bit-identical native affine transforms or introducing bilinear interpolation. [Earth Engine resampling guide](https://developers.google.com/earth-engine/guides/resample)

Earth Engine's images do not expose a separate final/preliminary flag in the checked properties. The comparison therefore identifies exact historical daily collection IDs and acquisition dates; it does not infer final status from the SAT catalog's “Near-Real-Time” title. The public CHC final archive offers an explicit final-stage path for a later all-years acquisition adapter. No preliminary collection is substituted during this assessment.

### Reproduce and audit

```sh
.venv/bin/python scripts/assess_chirps_migration.py \
  --project YOUR_PROJECT --as-of 2026-10-25 \
  --start 2024-01-01 --end 2024-02-29 --variants rnl sat
```

Use your own enabled project if needed. From `EDA/`, prefix the interpreter/script with `../`. The script limits requests to **92 development days**, rejects held-out periods before contacting the provider, requires complete local validated v2 dates, and downloads only isolated candidate subsets. It does not call a paid export, modify source configuration, or update v2 manifests.

Results for the checked-in script are stored locally at `data/interim/assessments/chirps_v3/c8a952c081e1e6c878036535/`: `comparison.json`, `daily_matched_means.csv`, and `daily_comparison.png`. Raw samples are cached separately under `cache/rnl/` and `cache/sat/`, with 18 seven-day-or-shorter chunks and checksummed identity receipts. They are gitignored, so a clean clone must reproduce them rather than assume the sample is bundled. The comparison JSON retains full-precision metrics, all received dates, missingness, collection/image IDs, acquisition times, projections, script hash and every input/candidate checksum. Repeated runs validate and reuse cached candidates. Corrupt caches are rejected; remove only the affected assessment chunk after inspection to reacquire it. Source fingerprints are checked again before results are published.

The v2 2024 archive SHA-256 was `d99d291f3828d2c282ed8f19cb0c46d8a2d3597c5d238594a0dc0d554431f962`; the province GeoJSON SHA-256 was `18c2a23343c6e0862d15d22835f4a8100bab9e327179e4a0ec72d3717d5420a9`. All five canonical v2 archives, their manifest, study configuration and province file retained their recorded checksums. Live acquisition succeeded, while credential-free fixtures independently cover matched missingness, development-only bounds, grid rejection, immutable source reuse and corrupt-cache detection.

## Migration gate

A source switch remains a separate decision. The concrete rebuild path is:

1. Preserve the v2 archives/configuration/builds and declare a new rainfall experiment with a named variant, stage, source-day convention and cutoff. Do not edit the existing v2 profile in place.
2. Add a v3 adapter and inventory schema that explicitly support that variant. The present `CHIRPSArchive` intentionally accepts only v2; changing a collection string alone will fail validation. Use the CHC explicit-final paths or verify the Earth Engine stage policy, retain per-image identifiers and revision checksums, and keep candidate downloads separate.
3. Acquire **all 2020–2024 training years and the frozen 2025 test year** consistently from that same v3 variant. Check actual published dates and interior gaps. Never splice v3 recent years into v2 training.
4. Recheck native alignment, missing masks and stable 5 km source mappings using training evidence only. Retain the candidate study mask and fallback limits; explicitly review any eligibility change caused by a new rainfall mask.
5. Rebuild keyed combined tables and split artifacts under a new preprocessing/build version. Refit ranges on training only, keep the fixed feature/category schema, regenerate development EDA and report exclusions. Old v2 scalers/models are not transferable automatically.
6. Evaluate daily-timing sensitivity and any model changes using development years only, choose the product/variant, retain the frozen 2025 test calendar and declare the evaluation protocol before opening outcomes. Model training is future authorized work, not an action performed here.
7. Treat the iOS operational predictor pipeline separately: measure observed release delays, archive actual issue-time inputs and evaluate lagged/forecast weather. Final/reanalysis data available later must not masquerade as inputs available at the historical forecast issue time.

This assessment does not authorize a rainfall source switch. See [dataset preparation](dataset-preparation.md) for current study limits and [the historical acquisition report](data-refresh-2026-10-25-report.md) for backfill evidence.
