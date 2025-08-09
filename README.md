# AutoFile

AI-assisted, bundle-aware intake for research projects on macOS.
Drop a mess in → get a tidy project with logs, manifests, and provenance—without breaking your structure.

* **Smart classify** via your local LLM (LM Studio/OpenAI-style API).
* **Bundle detection** for full code repos and manuscript trees.
* **Zero-drama Quick Action** (Finder right-click) with a single **swiftDialog** form.
* **Quarantine** low-confidence items; **append** to a daily manifest.
* **No junk**: ignores `.autofile.json`, cleans up temp wrappers, preserves your project skeleton.

---

# Table of contents

* [Features](#features)
* [Project layout (opinionated)](#project-layout-opinionated)
* [Requirements](#requirements)
* [Install](#install)
* [CLI usage](#cli-usage)
* [Finder Quick Action (swiftDialog)](#finder-quick-action-swiftdialog)
* [Configuration](#configuration)
* [Logs & outputs](#logs--outputs)
* [Privacy & safety](#privacy--safety)
* [Troubleshooting](#troubleshooting)
* [Roadmap](#roadmap)
* [License](#license)

---

# Features

* **AI intake**: calls a local OpenAI-style API (tested with LM Studio). Falls back to extension/keyword rules if the model fails.
* **Bundles kept intact**:

  * **Code repos** (e.g., `.git`, `pyproject.toml`, `package.json`, `src/`, etc.) → entire tree goes under `3_code/...`
  * **Manuscripts** (LaTeX **or** Word/PDF + Figures/Supplement/Supp Table, etc.) → entire tree goes under `5_manuscript/...`
* **Quarantine**: anything under your confidence threshold goes to `_intake_unsorted/…` for later review.
* **Append-only manifest**: per-day CSV gains rows across multiple runs, with a `batch_id` per run.
* **Friendly with Finder**: one pop-up (swiftDialog) to pick Project, Source, Apply/Dry-run, Move/Copy, Bundles, Quarantine threshold.
* **No surprises**:

  * Preserves original sub-folders under the right bucket.
  * Doesn’t import `.autofile.json`.
  * Leaves your project skeleton intact (re-touches canonical dirs after each run).
  * Deletes any temporary wrapper folders it created for single-file inputs.

---

# Project layout (opinionated)

```
Research/Projects/<PROJECT>/
  0_admin/
  1_proposals/
  2_data/
    raw/
    processed/
  3_code/
  4_analysis/
  5_manuscript/
  6_talks_posters/
  7_outputs/
```

AutoFile will place collaborator dumps under dated source folders, e.g.:

```
5_manuscript/_from_AliceLab_20250808/JC_Manuscript_v17/...
3_code/_from_BobLab_20250808/repo-name/...
2_data/raw/BioBank_20250808/...
0_admin/_intake_unsorted/...
```

---

# Requirements

* **macOS** (tested on Sequoia)
* **Python 3.10+** (recommend Homebrew Python: `/opt/homebrew/bin/python3`)
* **swiftDialog** for the one-window Quick Action UI:

  ```bash
  brew install --cask swift-dialog
  ```
* Optional (for AI classification):

  * **LM Studio** running a model and its **OpenAI Compatible Server**
  * A model id (e.g., `qwen/qwen3-coder-30b`) from `GET /v1/models`

---

# Install

1. Put the scripts somewhere stable (e.g., `~/Documents/bin/`):

   * `autofile.py`  (the main tool)
   * (optional) `new_project.py` if you also use a scaffolder

   ```bash
   mkdir -p ~/Documents/bin
   cp autofile.py ~/Documents/bin/
   chmod +x ~/Documents/bin/autofile.py
   ```

2. Make sure your research root exists:

   ```bash
   mkdir -p ~/Documents/Research/Projects
   ```

3. **(Recommended)** Grant **Full Disk Access** to:

   * **Automator**, **Finder**, and your terminal (Terminal/iTerm)
   * (optional) **LM Studio**

   System Settings → Privacy & Security → Full Disk Access

---

# CLI usage

Dry-run (plan only):

```bash
/opt/homebrew/bin/python3 ~/Documents/bin/autofile.py \
  --ai-intake "/path/to/dump" \
  --project "2025-CRISPR-MutSim" \
  --source "AliceLab"
```

Apply (copy), no content to the model (metadata only), small batches:

```bash
LMSTUDIO_API_BASE="http://127.0.0.1:1234/v1" \
LMSTUDIO_MODEL="YOUR_MODEL_ID" \
/opt/homebrew/bin/python3 ~/Documents/bin/autofile.py \
  --ai-intake "/path/to/dump" \
  --project "2025-CRISPR-MutSim" \
  --source "AliceLab" \
  --apply --no-content --batch-size 20
```

Move instead of copy + lower quarantine:

```bash
... --apply --move --quarantine-threshold 0.2
```

Use rule-based only (skip LLM):

```bash
... --apply --no-content --model ""   # or unset LMSTUDIO_*
```

### `.autofile.json` (for auto-intake mode)

```json
{
  "project": "2025-CRISPR-MutSim",
  "source": "AliceLab",
  "apply": true,
  "move": false,
  "bundle": ["code","manuscript"],
  "quarantine_threshold": 0.45,
  "use_ai": true
}
```

Run:

```bash
/opt/homebrew/bin/python3 ~/Documents/bin/autofile.py \
  --auto-intake "/path/to/folder/with/.autofile.json"
```

---

# Finder Quick Action (swiftDialog)

Create a **Quick Action** so you can right-click → **AutoFile: Intake Selection**.

1. **Automator → New Document → Quick Action**

   * “Workflow receives current”: **files or folders** in **Finder**
   * Add **Run Shell Script**

     * **Shell:** `/bin/zsh`
     * **Pass input:** **as arguments**

2. Paste this script (it prompts once, batches all selected items, runs AutoFile once, cleans up):

```zsh
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
  osascript -e 'display dialog "swiftDialog not found.\nInstall with:\nbrew install --cask swift-dialog" buttons {"OK"} default button 1' >/dev/null 2>&1 || true
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
```

3. (Optional) **Keyboard shortcut**
   System Settings → Keyboard → **Keyboard Shortcuts** → **Services/Quick Actions** → find your action → **Add Shortcut** (e.g., `⌃⌥⌘A`).

---

# Configuration

AutoFile reads flags and a few environment variables:

* `LMSTUDIO_API_BASE` (e.g., `http://127.0.0.1:1234/v1`)
* `LMSTUDIO_MODEL` (copy from `/v1/models`)
* `LMSTUDIO_AUTH` (if your server wants `Authorization:`; otherwise omit)

CLI flags (most useful):

```
--ai-intake <path>            # classify a folder with AI
--auto-intake <path>          # read .autofile.json inside a folder
--project <name>              # required (or provided via .autofile.json)
--source <label>              # e.g., AliceLab
--apply | --move              # copy vs move
--no-content                  # don't send text previews to the LLM
--batch-size <N>              # default 40
--peek-bytes <N>              # default 2000 (ignored with --no-content)
--bundle code,manuscript      # default: code,manuscript
--ignore-dirs <comma list>    # venv,.venv,__pycache__,node_modules,...
--quarantine-threshold <0..1> # default 0.45
```

---

# Logs & outputs

Inside the project:

* `autofile_plan_<source>_<YYYYMMDD>.jsonl` — decisions (one JSON per file)
* `autofile_manifest_<source>_<YYYYMMDD>.csv` — **appends** rows per run, includes `batch_id`
* `AUTOFILE_LOG.md` — lightweight summary (appends)

System log:

* `~/Library/Logs/AutoFile.log` — Quick Action + CLI chatter

---

# Privacy & safety

* **By default** AutoFile sends **only small text peeks** from text files (first N bytes) to the LLM.
  Use `--no-content` to send **zero** file contents (metadata only).
* Binary files are **never** sent.
* You control **Apply** vs **Dry run**, and **Copy** vs **Move**.

---

# Troubleshooting

* **No models / API fails**: make sure LM Studio is running and copy the exact **model id** from:

  ```bash
  curl -s http://127.0.0.1:1234/v1/models | jq -r '.data[].id'
  ```
* **PermissionError** writing plan/manifest: ensure your project dir is writable
  (and that your template wasn’t copied with read-only/immutable flags).
* **Automator uses the wrong Python**: in the Quick Action we call `/opt/homebrew/bin/python3`. Keep it that way.
* **Empty dropdown**: give Automator **Full Disk Access** and verify the path `~/Documents/Research/Projects` exists and contains folders.
* **Everything quarantined**: your threshold may be too high for rule-based items. Try `0.0–0.3`, or ensure the LLM is actually being called (check `AutoFile.log`).


---

# License

MIT
