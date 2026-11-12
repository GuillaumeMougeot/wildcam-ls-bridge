# Input CSV: SQL export

Reference: `resources/test_query.csv` (94 rows, kept outside git because it holds real
coordinates). A synthetic copy with the same 49 columns is in
`tests/fixtures/sample_query.csv`.

## Format rules (what `csv_ingest.py` accepts)

* UTF-8 (a BOM is fine). The delimiter is detected from the header: `,` `;` TAB or `|`.
* Missing values may be empty or `NULL` / `null` / `None` / `NaN`.
* **Required columns:** `media_id`, `file`. Everything else is optional.
* One row per *prediction*. Rows sharing a `media_id` become ONE Label Studio task, using the
  most confident prediction. Two `media_id`s pointing to the same file is an error.
* Rows whose `file_mediatype` is not `video/*` are skipped with a warning (the UI is a video UI).
* `file` must be a clean relative path: no leading `/`, no `..`, no backslash, no control
  characters. Otherwise the whole upload is rejected with HTTP 400.
* Limits: 20 MB, 20,000 rows (`BRIDGE_MAX_CSV_BYTES`, `BRIDGE_MAX_ROWS`).

## Columns

| Column | Used by the bridge | Notes |
|---|---|---|
| `prediction_id` | task data, results | UUID of the model prediction |
| `created_at`, `prediction_created_local` | – | prediction timestamps |
| `confidence` | prediction `score`, grouping | 0–1 |
| `prediction` | **pre-annotation** of `species_label` | e.g. `red deer`, `human`, `vehicle`, `bird`, `empty`, `undefined`, `badger` |
| `prediction_scientific_name`, `prediction_taxon_rank`, `top_1`, `top_1_scientific_name` | – | |
| `bbox_xmin` … `bbox_ymax`, `frame_image_path` | – | `NULL` in the sample |
| `model_name`, `model_version` | prediction `model_version` (`dfbsmdr-01.04.00`) | |
| `ensemble` | – | |
| `media_id` | **required**, task grouping key, results join key | UUID |
| `file` | **required**, ERDA path | `wildcams/data/<site>/<location>/<deployment>/img_NNNN.mp4` |
| `file_name` | task data | |
| `file_mediatype` | filter | `video/mp4` |
| `file_public`, `media_timestamp`, `capture_method`, `media_comments` | – | |
| `media_timestamp_local` | task data | |
| `exif_data` | – | large JSON (FileSize, Duration, ImageSize 1920x1080, 30 fps, …); field size limit raised to 10 MB |
| `deployment_id` | task data | |
| `deployment_start*`, `deployment_end*`, `camera_id`, `camera_model`, `bait_use`, `setup_by`, `timestamp_issues`, `deployment_comments` | – | |
| `location_id`, `latitude`, `longitude`, `elevation_m`, `coordinate_uncertainty_m`, `habitat` | – | coordinates are deliberately NOT copied into Label Studio |
| `location_name`, `site_name` | task data | e.g. `hoestemark_0001`, `tofte` |
| `site_id` | – | |

"Task data" columns appear as Data Manager columns in Label Studio and are returned in the
results, so annotations can be joined back to the database (`media_id`, `prediction_id`).

## Sample statistics (`test_query.csv`)

94 rows = 94 media = 94 files, all in
`wildcams/data/hoestemark/hoestemark_0001/hoestemark_0001_241217_250416/`.
Predictions: red deer 79, human 5, bird 4, vehicle 2, undefined 2, empty 1, badger 1. All of
them are valid choices in the labeling UI.
