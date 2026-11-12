import pytest

from wildcam_bridge.csv_ingest import parse_csv
from wildcam_bridge.label_config import load_label_config, parse_label_config
from wildcam_bridge.settings import Settings
from wildcam_bridge.tasks import build_tasks, map_prediction

JOB = "20261112T120000Z-abcdef"


def test_team_template_is_parsed(settings: Settings) -> None:
    cfg = load_label_config(settings.ls_label_config)
    assert cfg.data_keys == ("video",)
    assert cfg.control_names == [
        "species_label",
        "count",
        "certainty",
        "day_night",
        "age",
        "sex",
        "recurring_animal",
        "leashed",
        "horse_with_human",
        "notes",
    ]
    species = cfg.control("species_label")
    assert species is not None and species.to_name == "video"
    assert "red deer" in species.choices and len(species.choices) == 42


def test_config_without_controls_is_rejected() -> None:
    with pytest.raises(ValueError, match="no control"):
        parse_label_config('<View><Video name="v" value="$video"/></View>')


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("red deer", "red deer"),
        ("Red Deer", "red deer"),
        ("porcupine", "procupine"),
        ("wild_boar", "wild boar"),
        ("unicorn", None),
        (None, None),
        ("", None),
    ],
)
def test_map_prediction(label: str | None, expected: str | None) -> None:
    choices = ("red deer", "procupine", "wild boar")
    assert map_prediction(label, choices) == expected


def test_build_tasks(settings: Settings, sample_csv: bytes) -> None:
    items = parse_csv(sample_csv, strip="wildcams/").items
    cfg = load_label_config(settings.ls_label_config)
    tasks, warnings = build_tasks(items, job_id=JOB, settings=settings, label_config=cfg)

    assert len(tasks) == 4
    first = tasks[0]
    assert first["data"]["video"].startswith(f"/data/local-files/?d=bridge/{JOB}/data/testsite/")
    assert first["data"]["media_id"] == "m-0001"
    assert first["data"]["bridge_job_id"] == JOB
    assert first["predictions"] == [
        {
            "model_version": "dfbsmdr-01.04.00",
            "score": 0.97,
            "result": [
                {
                    "from_name": "species_label",
                    "to_name": "video",
                    "type": "choices",
                    "value": {"choices": ["red deer"]},
                }
            ],
        }
    ]
    assert "predictions" not in tasks[2]  # "unicorn" is not a UI choice
    assert tasks[2]["data"]["model_prediction"] == "unicorn"
    assert "predictions" not in tasks[3]  # no prediction at all
    assert "%20" in tasks[3]["data"]["video"]
    assert warnings == [
        "model label 'unicorn' is not a choice in the labeling UI "
        "(1 task(s) without pre-annotation)"
    ]


def test_build_tasks_unknown_control(settings: Settings, sample_csv: bytes) -> None:
    items = parse_csv(sample_csv, strip="wildcams/").items
    cfg = load_label_config(settings.ls_label_config)
    bad = settings.model_copy(update={"ls_prediction_from_name": "nope"})
    with pytest.raises(ValueError, match="nope"):
        build_tasks(items, job_id=JOB, settings=bad, label_config=cfg)


def test_csv_without_prediction_column_gives_blank_forms(settings: Settings) -> None:
    csv_text = "media_id,file\nm1,wildcams/data/a.mp4\nm2,wildcams/data/b.mp4\n"
    items = parse_csv(csv_text.encode(), strip="wildcams/").items
    cfg = load_label_config(settings.ls_label_config)
    tasks, warnings = build_tasks(items, job_id=JOB, settings=settings, label_config=cfg)
    assert [t.get("predictions") for t in tasks] == [None, None]
    assert [t["data"]["model_prediction"] for t in tasks] == [None, None]
    assert warnings == []
