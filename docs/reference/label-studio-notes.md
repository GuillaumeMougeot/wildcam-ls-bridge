# Label Studio notes

## The team guide (`resources/label-studio-guide-wildcam/`): summary

The guide explains how to run Label Studio **locally on Windows** (conda, `pip install
label-studio`). The server reproduces the same setup with `uv` and systemd. Points that still
apply:

1. **Local file serving** needs two variables, set before starting LS:
   `LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true` and
   `LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT=<root>`. The root must be the folder *above* the
   folder holding the data (guide example: `C:\WildCam_project`, not `...\Camera_trap_wildlife`).
   On the server: `/home/wildcam/labelstudio-data`.
2. **Create project → Labeling Setup → Custom template**: paste `UI_template.txt`. The bridge
   does this through the API with `config/label_config.xml` (a verbatim copy).
3. **Connect Cloud Storage → Local Files**: the storage path must be a sub-folder of the
   document root. *Import method = Tasks*, file filter = the `.json` task file, then
   *Save & Sync*. The bridge creates the storage only to grant media access and imports the
   tasks through the API (see below).
4. **Task video URLs** must look like `/data/local-files/?d=<path relative to document root>`.
   The bridge uses `?d=bridge/<job_id>/data/<site>/.../img_0001.mp4` (percent-encoded).
5. Troubleshooting from the guide: a video does not load when it is not under the storage
   folder, or when the path in the JSON is wrong.

## How the bridge talks to Label Studio 1.23 (verified against the installed source)

| Need | API | Note |
|---|---|---|
| auth (PAT) | `POST /api/token/refresh/ {"refresh": PAT}` → `{"access": …}`, then `Authorization: Bearer <access>` | the access token lives about 5 min; the client refreshes it on expiry or 401 |
| auth (legacy) | `Authorization: Token <key>` | disabled on the wildcam org |
| create project | `POST /api/projects {title ≤ 50 chars, description, label_config}` | |
| media permission | `POST /api/storages/localfiles {project, path, regex_filter, use_blob_urls:false}` | see below |
| import tasks | `POST /api/projects/{id}/import?return_task_ids=true` (JSON list) | **synchronous** in Community edition |
| show pre-annotations | `PATCH /api/projects/{id} {show_collab_predictions:true, model_version}` | |
| progress | `GET /api/projects/{id}` → `task_number`, `finished_task_number`, … | |
| results | `GET /api/projects/{id}/export?exportType=JSON&download_all_tasks=true` | in 1.23, `predictions` in the export are **ids only** |
| PAT minting (tests) | `POST /api/token/` → `{"token": …}` | **one PAT per user**: a second call returns 409 |

### Local-files permission check (important)

`io_storages/localfiles/views.py::localfiles_data` serves a file only if some
**LocalFilesImportStorage** has a `path` that is a *string prefix* of the file's folder,
**and** the user can access that storage's project. Consequences:

* every bridge project gets its own local storage pointing at `…/bridge/<job_id>`;
* job ids have a fixed length (`YYYYMMDDTHHMMSSZ-xxxxxx`), so no id is a prefix of another;
* the storage's `regex_filter` is `(?!)` (matches nothing), so an accidental *Sync* click
  cannot import duplicate tasks.

### Bootstrapping a throw-away instance (used by the e2e test)

```bash
LABEL_STUDIO_BASE_DATA_DIR=/tmp/ls LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true \
LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT=/tmp/media \
~/labelstudio/.venv/bin/label-studio start --no-browser --host 127.0.0.1 --port 18099 \
  --data-dir /tmp/ls --username e2e@example.org --password '…' \
  --user-token <40 hex> --enable-legacy-api-token
```

The first start takes about 80 s (database migrations). Health check: `GET /health` → `{"status": "UP"}`.

## Labeling interface (`config/label_config.xml` = `resources/UI_template.txt`)

`<Video name="video" value="$video"/>` plus these controls (all `toName="video"`). Each
control becomes one column in the results:

| Control | Type | Values |
|---|---|---|
| `species_label` | Choices (single) | 42 values: badger … wolverine, human, vehicle, undefined, empty |
| `count` | Number | 0–100, default 1 |
| `certainty` | Choices | Absolute / Moderate / Unsure |
| `day_night` | Choices | Day / Night |
| `age` | Choices | Adult / Juvenile |
| `sex` | Choices | Male / Female / Unsure |
| `recurring_animal` | Choices | Yes |
| `leashed` | Choices | Leashed / Not leashed (dogs) |
| `horse_with_human` | Choices | Human present / No human present |
| `notes` | TextArea | free text |

⚠️ The template spells **`procupine`** (sic). It was left unchanged because changing it
would change exported values. A model label `porcupine` is mapped onto it
(`tasks.LABEL_ALIASES`). Fix it in Label Studio *and* in `config/label_config.xml`
together if you want the correct spelling.
