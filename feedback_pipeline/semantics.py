"""Local semantic definitions; consumers retain context, policy and call order."""
import json
from pathlib import Path


def _load_definitions():
    data = json.loads(Path(__file__).with_name("semantic_definitions.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported semantic definition schema")
    if not isinstance(data.get("version"), str) or not data["version"]:
        raise ValueError("Semantic definitions need a version")
    if not isinstance(data.get("concepts"), dict) or not isinstance(data.get("aliases"), dict):
        raise ValueError("Semantic concepts and aliases must be objects")
    for key, concept in data["concepts"].items():
        if not key or not isinstance(concept, dict) or not isinstance(concept.get("name"), str) or not concept["name"]:
            raise ValueError("Semantic concepts need a nonempty key and name")
    for key, pairs in data["aliases"].items():
        if not key or not isinstance(pairs, list) or any(
            not isinstance(pair, list) or len(pair) != 2 or
            any(not isinstance(value, str) or not value for value in pair) for pair in pairs
        ):
            raise ValueError("Semantic aliases must be ordered nonempty string pairs")
        if len({pair[0] for pair in pairs}) != len(pairs):
            raise ValueError("Semantic alias source expressions must be unique within a group")
    return data


_DEFINITIONS = _load_definitions()


def concept_name(key: str) -> str:
    return _DEFINITIONS["concepts"][key]["name"]


def alias_pairs(group: str) -> tuple[tuple[str, str], ...]:
    """An ordered vocabulary, not a global normalizer or a topic classifier."""
    return tuple(tuple(pair) for pair in _DEFINITIONS["aliases"][group])
