"""Check that a project's tasks deliver the CSV model predictions to the labeling screen.

For every task, calls the endpoint the labeling UI uses when a task is opened
(``GET /api/tasks/{id}?project=``) and compares its prediction with the CSV row
(species, score = confidence, model version = the project's). Also checks the project
settings the pre-fill depends on and the "Label All Tasks" entry point
(``/api/projects/{id}/next``). Note: that call locks one task for the service account
for a while; run it on test projects.

    uv run python scripts/verify_prefill.py ~/resources/test_query.csv <project_id>

Exit code 0 = every video is pre-filled exactly as the CSV says (blank where the CSV has none).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from wildcam_bridge.label_config import load_label_config
from wildcam_bridge.labelstudio import LabelStudioClient
from wildcam_bridge.pipeline import default_ls_factory
from wildcam_bridge.settings import Settings
from wildcam_bridge.tasks import map_prediction


def _check_task(
    full: dict[str, Any], row: dict[str, str], project: dict[str, Any], choices: tuple[str, ...]
) -> tuple[list[str], str]:
    raw = None if row.get("prediction") in (None, "", "NULL") else row["prediction"]
    expected = map_prediction(raw, choices)
    preds = full["predictions"]
    got = preds[0]["result"][0]["value"]["choices"] if preds else None
    first = preds[0] if preds else None
    checks = {
        "one prediction": len(preds) == (1 if expected else 0),
        "species matches CSV": got == ([expected] if expected else None),
        "control species_label/video": first is None
        or (first["result"][0]["from_name"], first["result"][0]["to_name"])
        == ("species_label", "video"),
        "score == CSV confidence": first is None
        or abs(first["score"] - float(row["confidence"])) < 1e-9,
        "model_version == project's": first is None
        or first["model_version"] == project["model_version"],
        "no annotation yet": full.get("annotations", []) == [],
        "file == CSV file": full["data"]["file"] == row["file"],
    }
    label = got[0] if got else "<blank form>"
    return [k for k, ok in checks.items() if not ok], label


def verify(ls: LabelStudioClient, csv_path: Path, project_id: int, settings: Settings) -> int:
    control = load_label_config(settings.ls_label_config).control(settings.ls_prediction_from_name)
    choices = control.choices if control else ()
    with csv_path.open(encoding="utf-8") as fh:
        rows = {r["media_id"]: r for r in csv.DictReader(fh)}
    csv_counts = Counter(r.get("prediction") for r in rows.values())

    project = ls.get_project(project_id)
    print(f"project {project_id}: {project['title']!r}")
    for key in ("show_collab_predictions", "model_version", "reveal_preannotations_interactively"):
        print(f"  {key} = {project.get(key)!r}")
    settings_ok = (
        project["show_collab_predictions"] is True
        and project["reveal_preannotations_interactively"] is False
    )
    tasks = ls.request(
        "GET", "/api/tasks", params={"project": project_id, "page_size": 10_000}
    ).json()["tasks"]
    print(f"  tasks: {len(tasks)}  (CSV media: {len(rows)})")

    ok, problems, labels = 0, [], Counter[str]()
    for t in tasks:
        full = ls.request("GET", f"/api/tasks/{t['id']}", params={"project": project_id}).json()
        row = rows.pop(full["data"].get("media_id"), None)
        if row is None:
            problems.append(f"task {t['id']}: media_id not in CSV")
            continue
        bad, label = _check_task(full, row, project, choices)
        if bad:
            problems.append(f"task {t['id']} ({row.get('file_name')}): {bad}")
        else:
            ok += 1
            labels[label] += 1

    print(
        f"\nper-video: {ok}/{len(tasks)} OK, {len(problems)} problem(s), "
        f"CSV media without task: {len(rows)}"
    )
    for line in problems[:20]:
        print("  PROBLEM", line)
    print("pre-filled:", dict(labels.most_common()))
    print("CSV       :", dict(csv_counts.most_common()))

    nxt = ls.request("GET", f"/api/projects/{project_id}/next").json()
    stream = [x["result"][0]["value"] for x in nxt.get("predictions") or []]
    print(f"\n'Label All Tasks' first task {nxt['id']} ({nxt['data'].get('file_name')}): {stream}")
    print(f"open in browser: {settings.ls_public_url}/projects/{project_id}/data?task={nxt['id']}")
    return 0 if settings_ok and not problems and not rows else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("csv", type=Path)
    ap.add_argument("project_id", type=int)
    args = ap.parse_args()
    settings = Settings()
    with default_ls_factory(settings) as ls:
        return verify(ls, args.csv, args.project_id, settings)


if __name__ == "__main__":
    sys.exit(main())
