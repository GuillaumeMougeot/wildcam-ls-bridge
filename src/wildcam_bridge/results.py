"""Flatten a Label Studio JSON export into one row per annotation.

Columns = fixed bookkeeping columns + one column per control of the labeling UI (taken from
the label config, so adding a question to the template adds a column automatically).
Multiple choices are joined with ``"; "``.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from wildcam_bridge.label_config import LabelConfig

BASE_COLUMNS = (
    "project_id",
    "task_id",
    "media_id",
    "prediction_id",
    "file",
    "model_prediction",
    "model_confidence",
    "annotation_id",
    "annotator_id",
    "created_at",
    "updated_at",
    "lead_time_s",
    "was_cancelled",
    "ground_truth",
)

_VALUE_KEYS = ("choices", "number", "text", "rating", "labels", "taxonomy", "datetime")


def _value_to_cell(value: dict[str, Any]) -> Any:
    for key in _VALUE_KEYS:
        if key in value:
            v = value[key]
            if isinstance(v, list):
                return "; ".join(
                    "/".join(map(str, x)) if isinstance(x, list) else str(x) for x in v
                )
            return v
    return json.dumps(value, ensure_ascii=False)


def _annotator_id(completed_by: Any) -> Any:
    if isinstance(completed_by, dict):
        return completed_by.get("id")
    return completed_by


def columns_for(label_config: LabelConfig) -> list[str]:
    return [*BASE_COLUMNS, *label_config.control_names]


def flatten_export(
    tasks: list[dict[str, Any]],
    label_config: LabelConfig,
    *,
    project_id: int | None = None,
    include_unannotated: bool = False,
) -> list[dict[str, Any]]:
    controls = set(label_config.control_names)
    rows: list[dict[str, Any]] = []
    for task in tasks:
        data = task.get("data") or {}
        base = {
            "project_id": project_id if project_id is not None else task.get("project"),
            "task_id": task.get("id"),
            "media_id": data.get("media_id"),
            "prediction_id": data.get("prediction_id"),
            "file": data.get("file"),
            "model_prediction": data.get("model_prediction"),
            "model_confidence": data.get("model_confidence"),
        }
        annotations = task.get("annotations") or []
        if not annotations and include_unannotated:
            rows.append({**dict.fromkeys(columns_for(label_config)), **base})
        for ann in annotations:
            row: dict[str, Any] = {**dict.fromkeys(columns_for(label_config)), **base}
            row.update(
                annotation_id=ann.get("id"),
                annotator_id=_annotator_id(ann.get("completed_by")),
                created_at=ann.get("created_at"),
                updated_at=ann.get("updated_at"),
                lead_time_s=ann.get("lead_time"),
                was_cancelled=bool(ann.get("was_cancelled", False)),
                ground_truth=bool(ann.get("ground_truth", False)),
            )
            for item in ann.get("result") or []:
                name = item.get("from_name")
                if name in controls and isinstance(item.get("value"), dict):
                    row[name] = _value_to_cell(item["value"])
            rows.append(row)
    return rows


def rows_to_csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: "" if v is None else v for k, v in row.items()})
    return buf.getvalue()
