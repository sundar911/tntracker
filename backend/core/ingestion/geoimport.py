from __future__ import annotations

import json
from pathlib import Path


def load_geojson(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _prop_value(props: dict, *keys: str):
    for key in keys:
        if key in props:
            return props.get(key)
    return None


def iter_constituency_features(geojson: dict):
    for feature in geojson.get("features", []):
        props = feature.get("properties", {}) or {}
        name = _prop_value(props, "ac_name", "AC_NAME", "name", "NAME", "constituency", "CONSTITUENCY")
        number = _prop_value(
            props,
            "ac_no",
            "AC_NO",
            "number",
            "NUMBER",
            "constituency_no",
            "CONSTITUENCY_NO",
        )
        yield {
            "name": name.strip() if isinstance(name, str) else name,
            "number": int(number) if number not in (None, "") else None,
            "properties": props,
            "geometry": feature.get("geometry"),
        }
