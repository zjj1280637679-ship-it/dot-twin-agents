"""Closed public input grammar; no host state or I/O."""
import re

MODES = ("open", "directed", "verify", "consolidate")


def validate(value):
    if type(value) is not dict or not {"task_name", "delta"} <= set(value) or set(value) - {"task_name", "delta", "mode"}:
        raise ValueError("invalid_fields")
    name, delta, mode = value["task_name"], value["delta"], value.get("mode", "open")
    if type(name) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
        raise ValueError("invalid_task_name")
    if type(delta) is not str or not delta.strip():
        raise ValueError("invalid_delta")
    try:
        delta.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid_delta_unicode") from exc
    if "\x00" in delta:
        raise ValueError("invalid_delta_unicode")
    if type(mode) is not str or mode not in MODES:
        raise ValueError("invalid_mode")
    return name, delta, mode
