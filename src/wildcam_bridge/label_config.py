"""Read the Label Studio labeling interface (config/label_config.xml).

The template is the team's ``UI_template.txt``: a <Video name="video" value="$video"/> plus
controls (Choices, Number, TextArea). We only need the control names/types (to flatten the
results into columns) and the allowed choices (to map model predictions onto them).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET  # trusted local file, not user input
from dataclasses import dataclass, field
from pathlib import Path

CONTROL_TAGS = {
    "Choices",
    "Number",
    "TextArea",
    "Rating",
    "Labels",
    "Taxonomy",
    "DateTime",
    "VideoRectangle",
    "TimelineLabels",
}


@dataclass(frozen=True)
class Control:
    name: str
    tag: str
    to_name: str
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class LabelConfig:
    xml: str
    controls: tuple[Control, ...]
    data_keys: tuple[str, ...] = field(default=())  # e.g. ("video",) from value="$video"

    def control(self, name: str) -> Control | None:
        return next((c for c in self.controls if c.name == name), None)

    @property
    def control_names(self) -> list[str]:
        return [c.name for c in self.controls]


def parse_label_config(xml: str) -> LabelConfig:
    root = ET.fromstring(xml)  # noqa: S314 - trusted, version-controlled template
    controls: list[Control] = []
    data_keys: list[str] = []
    for el in root.iter():
        value = el.attrib.get("value", "")
        if value.startswith("$") and el.tag not in CONTROL_TAGS:
            data_keys.append(value[1:])
        if el.tag in CONTROL_TAGS and "name" in el.attrib:
            choices = tuple(c.attrib["value"] for c in el.iter("Choice") if "value" in c.attrib)
            controls.append(
                Control(
                    name=el.attrib["name"],
                    tag=el.tag,
                    to_name=el.attrib.get("toName", ""),
                    choices=choices,
                )
            )
    if not controls:
        raise ValueError("label config defines no control tags")
    return LabelConfig(xml=xml, controls=tuple(controls), data_keys=tuple(data_keys))


def load_label_config(path: Path) -> LabelConfig:
    return parse_label_config(path.read_text(encoding="utf-8"))
