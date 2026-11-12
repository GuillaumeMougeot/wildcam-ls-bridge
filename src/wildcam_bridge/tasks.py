"""Build Label Studio tasks (with model pre-annotations) from parsed CSV rows.

Task format, as in the team's original ``Selection.json``::

    {"data": {"video": "/data/local-files/?d=<path>"},
     "predictions": [{"model_version": "...", "result": [
         {"from_name": "species_label", "to_name": "video", "type": "choices",
          "value": {"choices": ["bird"]}}]}]}
"""

from __future__ import annotations

from typing import Any

from wildcam_bridge.csv_ingest import MediaItem
from wildcam_bridge.label_config import LabelConfig
from wildcam_bridge.paths import ls_local_files_url
from wildcam_bridge.settings import Settings

# Model label -> label-config choice, for labels that differ only in spelling.
# NB: the team template spells "procupine" (sic); we map the correct spelling onto it
# rather than silently changing the template (that would change exported values).
LABEL_ALIASES = {
    "porcupine": "procupine",
    "raccoon-dog": "raccoon dog",
    "red_deer": "red deer",
    "roe_deer": "roe deer",
    "wild_boar": "wild boar",
}


def map_prediction(label: str | None, allowed: tuple[str, ...]) -> str | None:
    """Return the label-config choice matching a model label, or None if there is none."""
    if not label:
        return None
    by_lower = {c.lower(): c for c in allowed}
    key = label.strip().lower()
    key = LABEL_ALIASES.get(key, key)
    return by_lower.get(key)


def build_tasks(
    items: list[MediaItem], *, job_id: str, settings: Settings, label_config: LabelConfig
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (tasks, warnings)."""
    control = label_config.control(settings.ls_prediction_from_name)
    if control is None:
        raise ValueError(f"label config has no control named {settings.ls_prediction_from_name!r}")
    # The media object's variable, e.g. "video" from <Video value="$video"/>.
    data_key = label_config.data_keys[0] if label_config.data_keys else "video"

    tasks: list[dict[str, Any]] = []
    unmapped: dict[str, int] = {}
    for item in items:
        data: dict[str, Any] = {
            data_key: ls_local_files_url(settings, job_id, item.rel_path),
            **item.metadata,
            "model_prediction": item.prediction,
            "model_confidence": item.confidence,
            "bridge_job_id": job_id,
        }
        task: dict[str, Any] = {"data": data}
        choice = map_prediction(item.prediction, control.choices)
        if choice is not None:
            prediction: dict[str, Any] = {
                "model_version": item.model or "model",
                "result": [
                    {
                        "from_name": control.name,
                        "to_name": control.to_name,
                        "type": control.tag.lower(),
                        "value": {"choices": [choice]},
                    }
                ],
            }
            if item.confidence is not None:
                prediction["score"] = item.confidence
            task["predictions"] = [prediction]
        elif item.prediction:
            unmapped[item.prediction] = unmapped.get(item.prediction, 0) + 1
        tasks.append(task)

    warnings = [
        f"model label {label!r} is not a choice in the labeling UI "
        f"({n} task(s) without pre-annotation)"
        for label, n in sorted(unmapped.items())
    ]
    return tasks, warnings
