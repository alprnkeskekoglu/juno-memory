MARKER = "# juno-shim"

SHIM = r"""#!/bin/sh
# juno-shim: written by "juno init"; safe to delete
cfg="${JUNO_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/juno/juno.toml}"
rd() { [ -r "$cfg" ] && sed -n "s/^$1 = \"\(.*\)\"\$/\1/p" "$cfg" | head -1; }
py="${JUNO_PYTHON:-$(rd python)}"
root="$(rd plugin_root)"
if [ -z "$root" ] || [ ! -f "$root/juno/cli.py" ]; then
  root=""
  if [ -n "$py" ] && [ -x "$py" ]; then
    root=$("$py" -I -S -c '
import json, os, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
best = ""
for key, value in (data.get("plugins") or {}).items():
    if key.startswith("juno@") and isinstance(value, list):
        for entry in value:
            path = entry.get("installPath") if isinstance(entry, dict) else None
            if path and os.path.isfile(os.path.join(path, "juno", "cli.py")):
                best = path
print(best)
' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json" 2>/dev/null)
  fi
fi
if [ -z "$py" ] || [ ! -x "$py" ] || [ -z "$root" ] || [ ! -f "$root/juno/cli.py" ]; then
  echo "Juno: plugin not found; run juno init" >&2
  exit 2
fi
exec "$py" -I -S "$root/juno/cli.py" "$@"
"""
