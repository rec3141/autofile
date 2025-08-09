#!/bin/zsh
set -euo pipefail

# --- unified cleanup (handles both lock + temp) ---
cleanup() {
  local rc=$?
  [[ -n "${batch:-}"   ]] && /bin/rm -rf -- "$batch"
  [[ -n "${LOCKDIR:-}" ]] && /bin/rmdir "$LOCKDIR" 2>/dev/null || true
  exit $rc
}
trap cleanup EXIT INT TERM

# --- Paths / env ---
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
PY="/opt/homebrew/bin/python3"
DIALOG="$(command -v dialog || true)"
SCRIPT="$HOME/Documents/bin/autofile.py"
PROJROOT="$HOME/Documents/Research/Projects"
LOG="$HOME/Library/Logs/AutoFile.log"

mkdir -p "$HOME/Library/Logs" "$PROJROOT"
echo "$(date) AutoFile QA start (swiftDialog) PY=$PY" >> "$LOG"

# Ensure swiftDialog exists
if [[ -z "$DIALOG" || ! -x "$DIALOG" ]]; then
  osascript -e 'display dialog "swiftDialog not found.\nInstall with:\nbrew install --cask swift-dialog" buttons {"OK"} default button 1' >/dev/null 2>&1 || true
  exit 1
fi

# --- single-instance guard + stale dialog cleanup ---
LOCKDIR="${TMPDIR%/}/autofile.lock"
until mkdir "$LOCKDIR" 2>/dev/null; do sleep 0.2; done

# Close any zombie "AutoFile Intake" dialogs left by a crashed run
/usr/bin/pkill -f '/usr/local/bin/dialog .*AutoFile Intake' >/dev/null 2>&1 || true

# Build project list and CSV for dropdown
PROJ_LIST="$(/bin/ls -1 "$PROJROOT" 2>/dev/null | /usr/bin/sort || true)"
PROJ_CSV="$(printf "%s" "$PROJ_LIST" | paste -sd, -)"
DEFAULT_PROJ="$(printf "%s" "$PROJ_LIST" | head -n1 || true)"

# Make PROJROOT visible to the Python helper
export PROJROOT

# ---- Single window form (swiftDialog via --jsonstring with defaults) ----
DDEF="$("$PY" - <<'PY'
import json, os, pathlib, datetime
root = pathlib.Path(os.environ["PROJROOT"]).expanduser()
root.mkdir(parents=True, exist_ok=True)
projs = sorted([p.name for p in root.iterdir() if p.is_dir()])

year = datetime.date.today().year
suggest = f"{year}-My-Project"

data = {
  "title": "AutoFile Intake",
  "message": "Choose destination and options for this batch.",
  "width": 700,
  "height": 500,
  "selectitems": [
    {"title":"Project","values": projs, **({"default": projs[0]} if projs else {})},
    {"title":"Apply","values": ["Apply now","Dry run"], "default":"Apply now"},
    {"title":"Originals","values": ["Copy","Move"], "default":"Copy"}
  ],
  "textfield": [
    {"title":"New Folder (overrides below)","value":""},
    {"title":"Source","value":"Eric"},
    {"title":"Confidence (0–1)","value":"0.45"}
  ],
  "checkbox": [
    {"label":"Use AI","checked": True},
    {"label":"Bundle: code","checked": True},
    {"label":"Bundle: manuscript","checked": True}
  ],
  "button1text": "OK",
  "button2text": "Cancel"
}
print(json.dumps(data, separators=(",",":")))
PY
)"

json="$("$DIALOG" --json --jsonstring "$DDEF")"; rc_dialog=$?
[[ $rc_dialog -ne 0 || -z "$json" ]] && exit 1

cfg="$("$PY" - "$json" <<'PY'
import json, sys
raw = json.loads(sys.argv[1])

def pick_select(key, default=""):
    v = raw.get(key, default)
    if isinstance(v, dict):
        return (v.get("selectedValue") or default).strip()
    return (v or default).strip()

selected_project = pick_select("Project")
typed_project    = (raw.get("New Folder (overrides below)") or "").strip()
project = typed_project or selected_project or "New-Project"

apply_sel = pick_select("Apply","Apply now")
orig_sel  = pick_select("Originals","Copy")

cfg = {
    "project": project,
    "source": raw.get("Source","Eric"),
    "apply":  (apply_sel.lower().startswith("apply")),   # True if "Apply now"
    "move":   (orig_sel.lower().startswith("move")),     # True if "Move"
    "bundle": [k.split(": ",1)[1] for k,v in raw.items()
               if k.startswith("Bundle: ") and v is True],
    "quarantine_threshold": float(str(raw.get("Confidence (0–1)","0.45")).strip() or 0.45),
    "use_ai": bool(raw.get("Use AI", True)),
}
print(json.dumps(cfg))
PY
)"



# Pull out project for later
proj="$("$PY" - "$cfg" <<'PY'
import json, sys; print(json.loads(sys.argv[1])["project"])
PY
)"

# Ensure project exists
mkdir -p "$PROJROOT/$proj"

# --- Build one batch folder with everything selected ---
WRAP_ROOT="${TMPDIR%/}/autofile"
mkdir -p "$WRAP_ROOT"
batch="$(/usr/bin/mktemp -d "$WRAP_ROOT/batch.XXXXXX")"

# Copy items in (keep originals; we only delete after success if "Move")
ORIGS=()
for f in "$@"; do
  ORIGS+=("$f")
  if [[ -d "$f" ]]; then
    /usr/bin/ditto "$f" "$batch/$(basename "$f")"
  else
    /bin/cp -p "$f" "$batch/"
  fi
done

printf "%s AutoFile: batch intake %d items → project %s (batch=%s)\n" "$(date)" "${#ORIGS[@]}" "$proj" "$batch" >> "$LOG"
print -r -- "$cfg" > "$batch/.autofile.json"

# Run AutoFile ONCE for the batch (sync so we can post-clean)
rc=0
"$PY" "$SCRIPT" --auto-intake "$batch" --project "$proj" >> "$LOG" 2>&1 || rc=$?

# If user chose Move and the run succeeded, delete originals
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
