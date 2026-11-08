import copy
import json
import os
from typing import Any

Record = dict[str, Any]

LIST_KEYS = ("additionalDirectories", "allow", "deny")


class SettingsError(Exception):
    pass


def permission_path(path: str, home: str) -> str:
    path = os.path.abspath(path)
    home = os.path.abspath(home)
    if path == home:
        return "~"
    if path.startswith(home.rstrip(os.sep) + os.sep):
        return "~" + path[len(home) :]
    return "/" + path


def build_block(vault: str, home: str) -> dict[str, list[str]]:
    spec = permission_path(vault, home)
    return {
        "additionalDirectories": [os.path.abspath(vault)],
        "allow": [f"Read({spec}/**)", f"Edit({spec}/**)"],
        "deny": [f"Edit({spec}/rules/**)", f"Edit({spec}/.git/**)"],
    }


def parse(raw: bytes) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise SettingsError(
            f"settings file is not strict JSON ({error}); refusing to rewrite it"
        ) from error
    if not isinstance(data, dict):
        raise SettingsError("settings top level is not a JSON object; refusing to rewrite it")
    return data


def detect_indent(raw: bytes) -> int | str:
    for line in raw.decode("utf-8", "replace").splitlines()[1:]:
        stripped = line.lstrip(" \t")
        if stripped and stripped != line:
            lead = line[: len(line) - len(stripped)]
            return "\t" if lead.startswith("\t") else len(lead)
    return 2


def dumps(data: dict[str, Any], indent: int | str, trailing_newline: bool = True) -> bytes:
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    return (text + ("\n" if trailing_newline else "")).encode("utf-8")


def merge(data: dict[str, Any], block: dict[str, list[str]]) -> tuple[dict[str, Any], Record]:
    new = copy.deepcopy(data)
    created: list[str] = []
    added: dict[str, list[str]] = {}
    if "permissions" not in new:
        new["permissions"] = {}
        created.append("permissions")
    perms = new["permissions"]
    if not isinstance(perms, dict):
        raise SettingsError("existing settings 'permissions' is not an object")
    for key in LIST_KEYS:
        if key not in perms:
            perms[key] = []
            created.append(f"permissions.{key}")
        elif not isinstance(perms[key], list):
            raise SettingsError(f"existing settings permissions.{key} is not a list")
        for entry in block[key]:
            if entry not in perms[key]:
                perms[key].append(entry)
                added.setdefault(key, []).append(entry)
    previous: Record = {"present": "autoMemoryEnabled" in new}
    if previous["present"]:
        previous["value"] = new["autoMemoryEnabled"]
    previous["changed"] = new.get("autoMemoryEnabled") is not False
    new["autoMemoryEnabled"] = False
    return new, {"added": added, "created": created, "autoMemoryEnabled": previous}


def merge_records(old: Record, extra: Record) -> Record:
    added = {k: list(v) for k, v in old.get("added", {}).items()}
    for key, entries in extra.get("added", {}).items():
        for entry in entries:
            if entry not in added.setdefault(key, []):
                added[key].append(entry)
    created = list(old.get("created", []))
    created += [c for c in extra.get("created", []) if c not in created]
    auto = old["autoMemoryEnabled"]
    if extra["autoMemoryEnabled"]["changed"] and not auto["changed"]:
        auto = extra["autoMemoryEnabled"]
    merged: Record = {"added": added, "created": created, "autoMemoryEnabled": auto}
    if "outputStyle" in old:
        merged["outputStyle"] = old["outputStyle"]
    return merged


def unmerge(data: dict[str, Any], record: Record) -> tuple[dict[str, Any], list[str]]:
    new = copy.deepcopy(data)
    notes: list[str] = []
    perms = new.get("permissions")
    for key, entries in record.get("added", {}).items():
        values = perms.get(key) if isinstance(perms, dict) else None
        for entry in entries:
            if isinstance(values, list) and entry in values:
                values.remove(entry)
            else:
                notes.append(f"permissions.{key} entry not found, left alone: {entry}")
    created = record.get("created", [])
    if isinstance(perms, dict):
        for key in LIST_KEYS:
            if f"permissions.{key}" in created and perms.get(key) == []:
                del perms[key]
        if "permissions" in created and perms == {}:
            del new["permissions"]
    auto = record.get("autoMemoryEnabled", {})
    if auto.get("changed"):
        if new.get("autoMemoryEnabled") is False:
            if auto.get("present"):
                new["autoMemoryEnabled"] = auto["value"]
            else:
                del new["autoMemoryEnabled"]
        else:
            notes.append("autoMemoryEnabled changed since install; left alone")
    if "outputStyle" in record:
        if new.get("outputStyle") == record["outputStyle"]:
            del new["outputStyle"]
        else:
            notes.append("outputStyle changed since install; left alone")
    return new, notes
