# Annual maps and the static settlement proxy

These commands reproduce the completed study's spatial inputs on explicit invocation. They are not a scheduled refresh service; see [findings and final scope](findings.md).

See [first-time setup](setup.md) for authentication, historical acquisition and the required BIG files. Use the shared study configuration after installing this repository editable (`python -m pip install -e . --no-deps`) in the existing environment. Commands work from the repository and its EDA directory. Earth Engine requires your existing free access and project for provider discovery/acquisition. Do not put credentials in configuration or source control.

```sh
python scripts/download_modis_landcover.py --project YOUR_PROJECT --profile expanded --start 2020-01-01 --end 2025-12-31 --as-of 2026-10-25
python scripts/download_ghsl_built_surface.py --project YOUR_PROJECT
python scripts/prepare_spatial_features.py --profile expanded --as-of 2026-10-25
```

From EDA use `../scripts/...`. MODIS discovery reads actual asset timestamps; it does not invent assets for requested daily years. Already validated map files are reused, older manifest entries retained, and a downloaded map is fully validated before the cumulative manifest is atomically published. Later failures preserve previous committed maps. Repeated downloads do not rewrite reused files. Unknown/corrupt caches fail closed; inspect diagnostics rather than deleting good data. Availability records describe current provider assets, never their historical release time. A cutoff fixes requested nominal dates, not an archived provider catalog snapshot.

Strict retrospective same-year assignment is the default. If a requested map is absent, feature preparation prints blocked years and stops. To deliberately reuse the latest previous map:

```sh
python scripts/prepare_spatial_features.py --profile expanded --as-of 2026-10-25 --policy carry_forward
```

Assignments record data year, actual map year, staleness, policy, and unknown release evidence. No future map is used for an earlier year. Development years are selected by default; `--include-held-out` is a separate explicit opt-in. Feature artifacts live beneath the profile/study build directory in a policy-specific subdirectory. Source hashes and the candidate grid hash identify their inputs; they do not overwrite legacy model tables. The [dataset preparation workflow](dataset-preparation.md) integrates these features into chronological tables.

The existing preprocessing-generated grid and manifest are required. Boundary checksums, lattice size/origin, CRS, mask policy and stable IDs must match. Candidate-mask approval remains a scientific decision. MODIS aggregation uses the existing `rasterio.warp.Resampling.mode` rule on the same study lattice, including the original full province-grid warp extent even when only candidate peat cells are emitted, with water retained as class 17 and missing output rejected. Classes are categorical; no continuous interpolation is introduced.

GHSL stays **2020 GHS-BUILT-S R2023A**, with the original buffered native 100 m subset. A valid cache requires no project or network access. Initial acquisition requires `--project YOUR_PROJECT`. Caches are checked by checksum, native grid, bands, CRS, dtype, nodata and values before reuse. The feature rule stays `built_surface > 0`, excluding 65535 nodata: nearest built-pixel center to each study-cell center in the study LAEA projection. This represents proximity to mapped structures, not all human activity. Epoch, source hash and proxy rule accompany every cell. No annual GHSL copies or nominal later-epoch substitution occurs.

`EDA/05_MODIS_LandCover_EDA.ipynb` reads validated development maps, reports missing years, and computes annual change only between consecutive observed years. Its first/last maps are labeled by actual map year. It never fills missing maps to create apparent changes. `EDA/06_GHSL_EDA.ipynb` validates and reuses the static raster. Its 1 km threshold comparisons remain explicitly exploratory and are separate from the native 100 m feature rule.

Validation uses tiny real GeoTIFF fixtures without credentials:

```sh
python -m unittest discover -s tests -p test_spatial.py -v
```

These cover discovery, missing years, strict/carry-forward policies, cumulative refresh/reuse, invalid downloads and cache rejection, mode aggregation on stable cells, static source reuse and 2020 distance provenance. Full live downloads and provider authentication are not needed for these tests.

Read-only regression against the existing local artifacts reproduces all 17,560 MODIS cell/year classes exactly and all 3,512 GHSL distances within numerical tolerance. Both updated EDA notebooks execute against the historical cache without changing source files.
