# kalimantan-peatguard
Peatland fire risk monitoring for Kalimantan using ML

## Preprocessing: candidate 5 km study grid

Run `preprocessing.ipynb` from the repository root with the project environment
after downloading the source data. This is the working preprocessing pipeline.
It reads the existing BIG province and peatland GeoJSON files under
`data/raw/boundaries/` and `data/raw/peatland/`, builds full 5,000 m squares in
a local equal-area projection, and flags intersections with the dissolved BIG
layer inside the five provinces. All 146 downloaded BIG polygons are included
as a candidate mask; their classification remains subject to review.

The notebook explains the projection, fixed lattice origin, stable cell IDs,
boundary-cell rule, and overlap fractions. It validates geometry and area
coverage, then saves a CSV, WGS84 GeoJSON, overview/close-up PNG, and source-hashed
manifest under `data/interim/study_grid_5km/` (excluded from Git). It then
constructs daily FIRMS labels and attaches CHIRPS rainfall and ERA5-Land weather
directly from their source-grid files. Review the grid map and mask against the
paper before adopting the resulting candidate dataset. The projection and
inclusion rules are documented reconstruction choices.

## Reproduce the dataset EDA

The notebooks inspect daily rainfall, air temperature, modeled surface soil water, annual land cover, and GHSL built-up surface relevant to 2020–2024. Downloaded data and their generated manifests are kept under `data/interim/` and excluded from Git. The scripts reproduce them.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/earthengine authenticate
.venv/bin/python scripts/download_chirps_v2.py --project kalimantan-peat
.venv/bin/python scripts/download_era5_land_daily.py --project kalimantan-peat
.venv/bin/python scripts/download_modis_landcover.py --project kalimantan-peat
.venv/bin/python scripts/download_ghsl_built_surface.py --project kalimantan-peat
```

The project ID must be an Earth Engine-enabled Google Cloud project accessible to your account. Open `03_CHIRPS_EDA.ipynb`, `04_ERA5_Land_EDA.ipynb`, `05_MODIS_LandCover_EDA.ipynb`, and `06_GHSL_EDA.ipynb` from the repository root after downloading. The CHIRPS, ERA5-Land, and GHSL scripts can resume from checkpoints if interrupted.

The ERA5-Land reconstruction uses daily UTC means of `temperature_2m` and `volumetric_soil_water_layer_1` (0–7 cm). The source paper names ERA5-Land at daily resolution but does not specify its exact bands, depth, daily statistic, or time zone. The preprocessing notebook assigns these values to the 5 × 5 km study cells using its documented nearest-source rule.

The MODIS reconstruction uses annual MCD12Q1 Version 6.1 `LC_Type1` (IGBP) maps, with `QC` and `LW` for quality checks. The paper does not identify its exact MODIS version, classification scheme, or map year. See [data source decisions](notes/data-decisions.md) for these choices and their limits.

The GHSL EDA uses the 2020 GHS-BUILT-S R2023A built-up surface product. It compares several tentative definitions of a settlement; the paper does not identify its GHSL layer, settlement threshold, or distance algorithm. No exploratory distance has been adopted as the final model feature.

## Current preprocessing output

Sections 14–16 of `preprocessing.ipynb` read the original CHIRPS and ERA5-Land
source-grid subsets, align them to the study cells, and add them to the daily
FIRMS table. For two coastal cells whose closest CHIRPS pixel is masked, the
notebook uses the nearest valid source pixel (4.27 and 4.55 km away). For 69
coastal cells whose closest ERA5-Land pixel is masked, the
notebook uses the nearest valid source pixel within 20 km.

Section 17 of the same notebook also saves
`data/interim/model_table/modis_landcover_annual_5km.csv`: the most common MODIS
IGBP class across each full 5 km square for every map year from 2020 to 2024.
This is a candidate reconstruction: the paper specifies a 5 × 5 km grid and
one-hot encoding, but does not identify the MODIS class band, the aggregation
method, or which annual map was assigned to each date. The candidate annual
classes are also saved separately for inspection.

Section 18 attaches the map from the same calendar year to each daily record
and creates one binary column per observed class. This is a documented choice
for retrospective reconstruction; the paper does not state this year-matching
rule, and a completed annual map may not have been available earlier that year.

Section 19 derives a candidate GHSL feature from the 2020 GHS-BUILT-S layer:
the projected distance from each study-cell center to the nearest 100 m source
pixel with any mapped built-up surface. The rule is documented in the notebook
because the paper does not identify its exact GHSL product, threshold, or
distance method. The notebook saves `data/interim/model_table/ghsl_distance_5km.csv`
and the combined table, `data/interim/model_table/daily_features_interim.parquet`.

Section 21 saves `train_2020_2023.parquet` and `test_2024.parquet` in the same
folder. Each contains the observed `fire_detected` target, one-hot land cover,
and four continuous inputs scaled with minimum and maximum values from the
training years only. The fitted ranges are in `training_scaling_ranges.csv`.
Two 2024 rainfall values exceed the training maximum, so their scaled values
are slightly above 1. These are same-day labels; 3- or 10-day forecast targets
and prediction-time feature availability still need to be defined. The
candidate spatial-feature choices also need review before model training.
