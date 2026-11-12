from pathlib import PurePosixPath

import pytest

from wildcam_bridge.csv_ingest import CsvValidationError, parse_csv


def test_sample_fixture_is_grouped_by_media(sample_csv: bytes) -> None:
    parsed = parse_csv(sample_csv, strip="wildcams/")
    assert parsed.n_rows == 5
    assert [i.media_id for i in parsed.items] == ["m-0001", "m-0002", "m-0003", "m-0004"]
    m2 = parsed.items[1]
    assert m2.prediction == "human"  # higher confidence (1.0) wins over vehicle (0.40)
    assert m2.confidence == 1.0
    assert m2.model == "dfbsmdr-01.04.00"
    assert parsed.items[0].rel_path == PurePosixPath(
        "data/testsite/site_0001/site_0001_241217_250416/img_0001.mp4"
    )
    assert parsed.items[3].prediction is None  # NULL -> None
    assert parsed.items[3].confidence is None
    assert any("grouped by media_id" in w for w in parsed.warnings)


def test_metadata_is_curated(sample_csv: bytes) -> None:
    item = parse_csv(sample_csv, strip="wildcams/").items[0]
    assert item.metadata["media_id"] == "m-0001"
    assert item.metadata["file"].startswith("wildcams/data/")
    assert "exif_data" not in item.metadata
    assert "latitude" not in item.metadata


def test_bom_semicolon_and_str_input() -> None:
    text = "﻿media_id;file;prediction\nm1;wildcams/data/a.mp4;fox\n"
    parsed = parse_csv(text.encode("utf-8"), strip="wildcams/")
    assert parsed.items[0].rel_path == PurePosixPath("data/a.mp4")
    assert parse_csv(text, strip="wildcams/").items[0].prediction == "fox"


@pytest.mark.parametrize(
    ("csv_text", "message"),
    [
        ("", "empty"),
        ("media_id,other\n1,2\n", "missing required column"),
        ("media_id,file\n1,../../etc/passwd\n", "unsafe file path"),
        ("media_id,file\n1,/abs/path.mp4\n", "unsafe file path"),
        ("media_id,file\n,\n", "no usable video rows"),
        ("media_id,file\nm1,a.mp4\nm1,b.mp4\n", "two different files"),
        ("media_id,file\nm1,a.mp4\nm2,a.mp4\n", "same file"),
    ],
)
def test_invalid_csv(csv_text: str, message: str) -> None:
    with pytest.raises(CsvValidationError, match=message):
        parse_csv(csv_text.encode())


def test_non_utf8_is_rejected() -> None:
    with pytest.raises(CsvValidationError, match="UTF-8"):
        parse_csv("media_id,file\nm1,H\xf8st.mp4\n".encode("latin-1"))


def test_row_limit() -> None:
    body = "media_id,file\n" + "".join(f"m{i},f{i}.mp4\n" for i in range(11))
    with pytest.raises(CsvValidationError, match="more than 10 rows"):
        parse_csv(body.encode(), max_rows=10)


def test_non_video_rows_are_skipped_with_warning() -> None:
    body = "media_id,file,file_mediatype\nm1,a.jpg,image/jpeg\nm2,b.mp4,video/mp4\n"
    parsed = parse_csv(body.encode())
    assert [i.media_id for i in parsed.items] == ["m2"]
    assert any("image/jpeg" in w for w in parsed.warnings)
