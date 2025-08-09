#!/bin/zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
PY="/opt/homebrew/bin/python3"
DIALOG="$(command -v dialog || true)"

SCRIPT="$HOME/Documents/bin/autofile.py"
PROJROOT="$HOME/Documents/Research/Projects"
LOG="$HOME/Library/Logs/AutoFile.log"

mkdir -p "$HOME/Library/Logs" "$PROJROOT"

if [[ -z "$DIALOG" || ! -x "$DIALOG" ]]; then
  osascript -e 'display dialog "swiftDialog not found.\nInstall at https://github.com/swiftDialog/swiftDialog" buttons {"OK"} default button 1' >/dev/null 2>&1 || true
  exit 1
fi

export PROJROOT

# ----- Build dialog definition with defaults -----
DDEF="$("$PY" - <<'PY'
import json, os, pathlib, datetime
root = pathlib.Path(os.environ["PROJROOT"]).expanduser()
root.mkdir(parents=True, exist_ok=True)
projs = sorted([p.name for p in root.iterdir() if p.is_dir()], key=str.casefold)
year  = datetime.date.today().year

data = {
  "title": "AutoFile Intake",
  "message": "Choose destination and options for this batch.",
  "width": 620,
  "selectitems": [
    {"title":"Project","values": projs, **({"default": projs[0]} if projs else {})},
    {"title":"Apply","values": ["Apply now","Dry run"], "default":"Apply now"},
    {"title":"Originals","values": ["Copy","Move"], "default":"Copy"}
  ],
  "textfield": [
    {"title":"New project (optional)","value":""},
    {"title":"Source label","value":"Manual"},
    {"title":"Quarantine (0–1)","value":"0.45"}
  ],
  "checkbox": [
    {"label":"Use AI","checked": True},
    {"label":"Bundle: code","checked": True},
    {"label":"Bundle: manuscript","checked": True}
  ],
  "button1text":"OK","button2text":"Cancel"
}
print(json.dumps(data, separators=(",",":")))
PY
)"

json="$("$DIALOG" --json --jsonstring "$DDEF")"; rc=$?
[[ $rc -ne 0 || -z "$json" ]] && exit 1

cfg="$("$PY" - "$json" <<'PY'
import json, sys
raw = json.loads(sys.argv[1])

def pick(key, default=""):
    v = raw.get(key, default)
    if isinstance(v, dict):
        return (v.get("selectedValue") or default).strip()
    return (v or default).strip()

selected_project = pick("Project")
typed_project    = (raw.get("New project (optional)") or "").strip()
project = typed_project or selected_project or "New-Project"
apply_now = pick("Apply","Apply now").lower().startswith("apply")
move_orig = pick("Originals","Copy").lower().startswith("move")

cfg = {
  "project": project,
  "source": raw.get("Source label","Manual"),
  "apply":  apply_now,
  "move":   move_orig,
  "bundle": [k.split(": ",1)[1] for k,v in raw.items()
             if k.startswith("Bundle: ") and v is True],
  "quarantine_threshold": float(str(raw.get("Quarantine (0–1)","0.45")).strip() or 0.45),
  "use_ai": bool(raw.get("Use AI", True)),
}
print(json.dumps(cfg))
PY
)"

proj="$("$PY" - "$cfg" <<'PY'
import json, sys; print(json.loads(sys.argv[1])["project"])
PY
)"
mkdir -p "$PROJROOT/$proj"

# ----- Batch the selection into a temp folder -----
WRAP_ROOT="${TMPDIR%/}/autofile"
mkdir -p "$WRAP_ROOT"
batch="$(/usr/bin/mktemp -d "$WRAP_ROOT/batch.XXXXXX")"
trap '[[ -n "${batch:-}" ]] && /bin/rm -rf -- "$batch"' EXIT

ORIGS=()
for f in "$@"; do
  ORIGS+=("$f")
  if [[ -d "$f" ]]; then /usr/bin/ditto "$f" "$batch/$(basename "$f")"
  else /bin/cp -p "$f" "$batch/"
  fi
done

print -r -- "$cfg" > "$batch/.autofile.json"

# ----- Run AutoFile once for the batch -----
rc=0
"$PY" "$SCRIPT" --auto-intake "$batch" --project "$proj" >> "$LOG" 2>&1 || rc=$?

# If user chose Move and run succeeded, remove originals
move_flag="$("$PY" - "$cfg" <<'PY'
import json, sys; print("1" if json.loads(sys.argv[1]).get("move") else "0")
PY
)"
if [[ "$move_flag" == "1" && "$rc" -eq 0 ]]; then
  for f in "${ORIGS[@]}"; do
    if [[ -d "$f" ]]; then /bin/rm -rf -- "$f"; else /bin/rm -f -- "$f"; fi
  done
fi

exit "$rc"
