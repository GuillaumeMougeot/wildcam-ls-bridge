# 09: Pre-fill test with the full `test_query.csv`

**Date:** 2026-11-12 · **Status:** done ✅

## Goal
On the owner's request: run the full `resources/test_query.csv` through the deployed service and
check that every model prediction is pre-filled in Label Studio.

## Run
`wildcam-bridge client submit ~/resources/test_query.csv --title "[TEST] pre-fill check full CSV" --wait`
through the public URL `https://wildcam.ecoscience.dk/bridge` → job `20261112T143030Z-1b356f`,
`ready` in 89 s, 94/94 videos (1.36 GB), no warnings, LS **project 6**.

## Check: `scripts/verify_prefill.py ~/resources/test_query.csv 6` (new, reusable)
For each task it calls the endpoint the labeling screen uses when a task is opened and
compares the prediction with the CSV row.

| Check | Result |
|---|---|
| project settings | `show_collab_predictions=True`, `model_version='dfbsmdr-01.04.00'`, `reveal_preannotations_interactively=False` |
| per video: species = CSV, score = CSV confidence, model version = project's, control `species_label`→`video`, no annotation yet, same file | **94/94 OK** |
| pre-filled distribution | red deer 79, human 5, bird 4, vehicle 2, undefined 2, empty 1, badger 1 (identical to the CSV) |
| "Label All Tasks" entry point (`/api/projects/6/next`) | first task img_0093.mp4 → `human` (CSV: `human`) |

Combined with the frontend logic verified in entry 08 (`addAnnotationFromPrediction` when a
task has no annotation and has predictions), every video opens with the model's species
pre-selected.

Not covered by this CSV: rows **without** a prediction (all 94 have one). That case (blank
form) is covered by the automated tests (`NULL` row in the fixture, CSV without a
`prediction` column).

Side effect: the `next` call locks task 6014 of project 6 for the service account for a
while (test project, no impact).

## Test leftovers to delete when done (owner)
LS projects **4, 5, 6** and the folders `~/labelstudio-data/bridge/20261112T140452Z-3a357f`,
`…/20261112T142121Z-cb212c` and `…/20261112T143030Z-1b356f` (about 2.7 GB in total).
