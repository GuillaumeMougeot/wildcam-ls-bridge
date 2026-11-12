"""Parse the CSV exported from the wildcam SQL database.

Expected format (see docs/reference/csv-schema.md): one row per model prediction, with at
least the columns ``media_id`` and ``file``. The SQL export writes ``NULL`` for missing
values and stores EXIF metadata as a large JSON string in ``exif_data`` (that is why the
csv field size limit is raised).

Several rows may refer to the same media file (e.g. several predictions/detections). They
are grouped into ONE Label Studio task per media file, keeping the most confident prediction.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from wildcam_bridge.paths import UnsafePathError, sanitize_relative, strip_prefix

csv.field_size_limit(10_000_000)

REQUIRED_COLUMNS = ("media_id", "file")
NULL_VALUES = {"", "NULL", "null", "None", "NaN", "nan"}
_DELIMITER_CANDIDATES = (",", ";", "\t", "|")

# CSV columns copied into the Label Studio task `data` (shown as Data Manager columns
# and returned with the results). Keep this list small: the full row stays in the SQL DB.
TASK_METADATA_COLUMNS = (
    "media_id",
    "prediction_id",
    "file",
    "file_name",
    "media_timestamp_local",
    "deployment_id",
    "location_name",
    "site_name",
)


class CsvValidationError(ValueError):
    """The uploaded CSV cannot be processed. The message is safe to return to the client."""


@dataclass(frozen=True)
class MediaItem:
    media_id: str
    file: str  # value of the CSV `file` column, untouched
    rel_path: PurePosixPath  # sanitised path relative to the ERDA root (prefix stripped)
    prediction: str | None = None
    confidence: float | None = None
    model: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class ParsedSelection:
    items: list[MediaItem]
    n_rows: int
    warnings: list[str]


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return None if value in NULL_VALUES else value


def _detect_delimiter(header_line: str) -> str:
    counts = {d: header_line.count(d) for d in _DELIMITER_CANDIDATES}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] > 0 else ","


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_csv(data: bytes | str, *, strip: str = "", max_rows: int = 20_000) -> ParsedSelection:
    """Validate and parse the CSV into one :class:`MediaItem` per media file."""
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CsvValidationError("CSV must be UTF-8 encoded") from exc
    else:
        text = data.removeprefix("﻿")
    if not text.strip():
        raise CsvValidationError("CSV is empty")

    header_line = text.split("\n", 1)[0]
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=_detect_delimiter(header_line))
    columns = [c.strip() for c in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise CsvValidationError(f"CSV is missing required column(s): {', '.join(missing)}")

    warnings: list[str] = []
    best: dict[str, MediaItem] = {}
    order: list[str] = []
    n_rows = 0
    try:
        for line_no, raw in enumerate(reader, start=2):
            n_rows += 1
            if n_rows > max_rows:
                raise CsvValidationError(f"CSV has more than {max_rows} rows")
            row = {(k or "").strip(): v for k, v in raw.items()}
            media_id = _clean(row.get("media_id"))
            file_value = _clean(row.get("file"))
            if not media_id or not file_value:
                warnings.append(f"line {line_no}: empty media_id or file, row skipped")
                continue
            mediatype = _clean(row.get("file_mediatype"))
            if mediatype and not mediatype.startswith("video/"):
                warnings.append(f"line {line_no}: {mediatype} is not a video, row skipped")
                continue
            try:
                rel = sanitize_relative(strip_prefix(file_value, strip))
            except UnsafePathError as exc:
                raise CsvValidationError(f"line {line_no}: unsafe file path ({exc})") from exc

            model_name, model_version = (
                _clean(row.get("model_name")),
                _clean(row.get("model_version")),
            )
            item = MediaItem(
                media_id=media_id,
                file=file_value,
                rel_path=rel,
                prediction=_clean(row.get("prediction")),
                confidence=_to_float(_clean(row.get("confidence"))),
                model="-".join(p for p in (model_name, model_version) if p) or None,
                metadata={c: v for c in TASK_METADATA_COLUMNS if (v := _clean(row.get(c)))},
            )
            previous = best.get(media_id)
            if previous is None:
                best[media_id] = item
                order.append(media_id)
                continue
            if previous.rel_path != item.rel_path:
                raise CsvValidationError(
                    f"line {line_no}: media_id {media_id} refers to two different files"
                )
            if (item.confidence or 0.0) > (previous.confidence or 0.0):
                best[media_id] = item
    except csv.Error as exc:
        raise CsvValidationError(f"malformed CSV: {exc}") from exc

    items = [best[m] for m in order]
    duplicates = n_rows - len(items) - sum("skipped" in w for w in warnings)
    if duplicates > 0:
        warnings.append(
            f"{duplicates} extra prediction row(s) grouped by media_id (highest confidence kept)"
        )
    seen_paths: dict[PurePosixPath, str] = {}
    for item in items:
        other = seen_paths.setdefault(item.rel_path, item.media_id)
        if other != item.media_id:
            raise CsvValidationError(
                f"media_id {other} and {item.media_id} point to the same file {item.rel_path}"
            )
    if not items:
        raise CsvValidationError("CSV contains no usable video rows")
    return ParsedSelection(items=items, n_rows=n_rows, warnings=warnings)
