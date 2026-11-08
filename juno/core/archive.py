import posixpath

ROOTS = ("vault", "config", "state")
INFO_NAME = "juno-export.json"
KINDS = ("file", "dir", "symlink")


def member_error(name: str, kind: str, linkname: str = "") -> str | None:
    if kind not in KINDS:
        return f"{name}: unsupported member type {kind}"
    if name.startswith("/") or "\\" in name:
        return f"{name}: absolute or unsafe path"
    parts = name.split("/")
    if ".." in parts:
        return f"{name}: path escapes the archive"
    top = parts[0]
    if name == INFO_NAME:
        return None if kind == "file" else f"{name}: must be a file"
    if top not in ROOTS:
        return f"{name}: unexpected top-level entry"
    if kind == "symlink":
        if linkname.startswith("/"):
            return f"{name}: symlink target is absolute"
        target = posixpath.normpath(posixpath.join(posixpath.dirname(name), linkname))
        if target == ".." or target.startswith("../") or target.split("/")[0] != top:
            return f"{name}: symlink leaves its folder"
    return None
