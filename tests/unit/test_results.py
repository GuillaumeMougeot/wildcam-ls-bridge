import csv
import io
import json
from pathlib import Path

from wildcam_bridge.label_config import load_label_config
from wildcam_bridge.results import BASE_COLUMNS, columns_for, flatten_export, rows_to_csv
from wildcam_bridge.settings import Settings

EXPORT = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "ls_export_sample.json").read_text()
)


def test_flatten_one_row_per_annotation(settings: Settings) -> None:
    cfg = load_label_config(settings.ls_label_config)
    rows = flatten_export(EXPORT, cfg, project_id=7)
    assert len(rows) == 3  # task 1: two annotators, task 2: one, task 3: none
    r = rows[0]
    assert r["project_id"] == 7
    assert r["media_id"] == "m-0001"
    assert r["species_label"] == "red deer"
    assert r["count"] == 2
    assert r["certainty"] == "Absolute"
    assert r["notes"] == "two hinds; one calf?"
    assert r["annotator_id"] == 1
    assert r["was_cancelled"] is False
    assert rows[1]["annotator_id"] == 5  # completed_by given as dict
    assert rows[2]["species_label"] == "fox; badger"  # multi-choice joined
    assert rows[2]["leashed"] is None  # unanswered control


def test_include_unannotated(settings: Settings) -> None:
    cfg = load_label_config(settings.ls_label_config)
    rows = flatten_export(EXPORT, cfg, include_unannotated=True)
    assert len(rows) == 4
    assert rows[-1]["media_id"] == "m-0003"
    assert rows[-1]["annotation_id"] is None
    assert rows[-1]["project_id"] == 3  # taken from the task when not given


def test_csv_output(settings: Settings) -> None:
    cfg = load_label_config(settings.ls_label_config)
    cols = columns_for(cfg)
    assert cols[: len(BASE_COLUMNS)] == list(BASE_COLUMNS)
    text = rows_to_csv(flatten_export(EXPORT, cfg), cols)
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert len(parsed) == 3
    assert parsed[0]["species_label"] == "red deer"
    assert parsed[0]["leashed"] == ""
