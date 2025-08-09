#!/usr/bin/env python3
"""
AutoFile — AI-assisted, bundle-aware collaborator dump intake for macOS (and others).

Highlights
- Classify with a local OpenAI-style API (LM Studio, e.g., qwen/qwen3-coder-30b).
- Preserve directory structures under the right bucket.
- **Bundle mode**: keep whole code repos or manuscript trees intact.
- Quarantine low-confidence classifications.
- Optional **auto-intake**: drop a folder with `.autofile.json` into a watched "AutoFile" folder.

-------------------------------------------------------------------------------
Setup (one-time)
-------------------------------------------------------------------------------
1) Ensure your base documents structure exists (Teaching/Research/... with Project_Template).
   You can run your prior script: `python new_project.py --setup --owner "Your Name"`.

2) Create a per-project folder under Research/Projects, e.g., "2025-CRISPR-MutSim".

-------------------------------------------------------------------------------
Common usage
-------------------------------------------------------------------------------
Dry-run (create plan only):
  python autofile.py --ai-intake "/path/to/dump" --project "2025-CRISPR-MutSim" --source "AliceLab"

Apply plan (copy files into project):
  python autofile.py --ai-intake "/path/to/dump" --project "2025-CRISPR-MutSim" --source "AliceLab" --apply

Move (destructive) instead of copy:
  python autofile.py --ai-intake "/path/to/dump" --project "2025-CRISPR-MutSim" --source "AliceLab" --apply --move

Bundle options (on by default):
  --bundle code,manuscript      # bundle code repos and manuscript trees
  --ignore-dirs venv,.venv,__pycache__,node_modules,dist,build,.ipynb_checkpoints

Quarantine low-confidence:
  --quarantine-threshold 0.45   # anything below goes to _intake_unsorted

Privacy:
  --no-content                  # do not send any file text to the model (metadata only)
  --peek-bytes 2000             # cap text preview size

-------------------------------------------------------------------------------
Auto-intake (for a watched folder)
-------------------------------------------------------------------------------
Drop a folder containing a small `.autofile.json` file:
{
  "project": "2025-CRISPR-MutSim",
  "source": "AliceLab",
  "apply": true,
  "move": false,
  "bundle": ["code","manuscript"],
  "quarantine_threshold": 0.45,
  "use_ai": true
}
Then run:
  python autofile.py --auto-intake "/path/to/dropped/folder"

-------------------------------------------------------------------------------
"""
import argparse
import datetime as _dt
import json
import mimetypes
import os
from pathlib import Path
import shutil
import sys
import csv
from datetime import datetime

# Optional: requests, else fallback to urllib
try:
    import requests  # type: ignore
except Exception:
    requests = None
import urllib.request
import urllib.error

# --------------------------- Constants ---------------------------------------

DEFAULT_API_BASE = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "deepseek/deepseek-r1-0528-qwen3-8b"
DEFAULT_AUTH = "Bearer lm-studio"

CATEGORY_KEYS = {"admin","proposals","data","code","talks","manuscript","unknown","ignore"}

AUTOFILE_CONFIG_NAMES = {".autofile.json", "autofile.json", "_autofile.json"}

TEXT_EXT = {
    ".txt",".md",".rst",".tex",".bib",".csv",".tsv",".json",".yaml",".yml",".ini",".cfg",".toml",
    ".py",".r",".R",".ipynb",".m",".jl",".sh",".bash",".ps1",".bat",".sql",".log"
}

DATA_EXT = {
    ".csv",".tsv",".xlsx",".xls",".parquet",".h5",".hdf5",".feather",".rds",".rdata",".sav",".dta",".mat",
    ".gz",".zip",".fastq",".fq",".bam",".sam",".vcf",".tif",".tiff",".nii",".nii.gz"
}
CODE_EXT = {".py",".r",".R",".ipynb",".m",".jl",".sh",".bash",".bat",".ps1",".sql",".yaml",".yml",".toml",".json"}
TALKS_EXT = {".ppt",".pptx",".key",".pdf"}
MANUSCRIPT_EXT = {".tex",".bib",".doc",".docx",".rtf",".odt",".pdf",".svg",".eps",".png",".jpg",".jpeg",".tif",".tiff"}

DEFAULT_IGNORE_DIRS = {"venv",".venv","__pycache__","node_modules","dist","build",".ipynb_checkpoints",".mypy_cache",".pytest_cache",".Rproj.user",".idea",".vscode"}

SYSTEM_PROMPT = """You are a meticulous file-intake classifier for an academic research lab.
Decide which category each file belongs to, based on filename, extension, size, and a small text preview when available.
Pick exactly one category from:
- admin        : IRB/ethics, MTAs, DUAs, NDAs, contracts, budgets, invoices, agreements
- proposals    : proposal/grant material, biosketches, specific aims, narratives
- data         : datasets (CSV/TSV/XLSX/Parquet/HDF5/FASTQ/BAM/VCF/TIFF/NIfTI/etc.)
- code         : scripts, notebooks, configs (py, R, ipynb, m, jl, sh, sql, yaml, toml, json)
- talks        : slides/posters/decks (ppt/pptx/key/pdf if clearly talk/poster)
- manuscript   : manuscripts, LaTeX, figures, submission/rebuttal docs
- unknown      : unclear; send to quarantine
- ignore       : junk or generated files we should skip (e.g., .DS_Store, Thumbs.db, cache, tmp).

Output STRICTLY in JSON Lines (JSONL), one object per file we give you, with this schema:
{"id": "<opaque id we provide>", "category":"admin|proposals|data|code|talks|manuscript|unknown|ignore", "confidence": 0.0-1.0, "reason": "short rationale", "rename": "optional new safe filename or empty string"}

Never include code fences, markdown, or extra prose. Only JSONL.
If uncertain, choose 'unknown' with moderate confidence and explain why in 'reason'."""

# --------------------------- Utilities ---------------------------------------

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

SKELETON_DIRS = [
    "0_admin",
    "1_proposals",
    os.path.join("2_data", "raw"),
    os.path.join("2_data", "processed"),
    "3_code",
    "4_analysis",
    "5_manuscript",
    "6_talks_posters",
    "7_outputs",
]

def ensure_project_skeleton(project_dir: Path):
    for rel in SKELETON_DIRS:
        ensure_dir(project_dir / rel)


def prune_empty_children(root: Path):
    # Remove empty dirs beneath root (but not root itself)
    for d in sorted([p for p in root.rglob("*") if p.is_dir()], reverse=True):
        try:
            next(d.iterdir())
        except StopIteration:
            try: d.rmdir()
            except Exception: pass

def safe_relpath(child: Path, parent: Path) -> Path:
    try:
        return child.relative_to(parent)
    except Exception:
        return Path(child.name)

def have_requests():
    return requests is not None

def post_chat_completion(api_base: str, model: str, messages: list, timeout: int = 60):
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1024,
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": os.environ.get("LMSTUDIO_AUTH", DEFAULT_AUTH),
    }

    data = json.dumps(payload).encode("utf-8")

    if have_requests():
        resp = requests.post(url, headers=headers, data=data, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    else:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as f:
                body = f.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTPError {e.code}: {e.read().decode('utf-8', errors='ignore')}") from e

def is_textlike(path: Path):
    if path.suffix.lower() in TEXT_EXT:
        return True
    mtype, _ = mimetypes.guess_type(str(path))
    return (mtype or "").startswith("text/")

def preview_text(path: Path, max_bytes: int = 2000):
    try:
        with path.open("rb") as f:
            data = f.read(max_bytes)
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""

# --------------------------- Bundle detection --------------------------------

def is_code_repo_root(p: Path) -> bool:
    markers = [".git","pyproject.toml","requirements.txt","setup.py","environment.yml","package.json",
               "Cargo.toml","Makefile",".Rproj",".Rproj.user","src"]
    for m in markers:
        if (p / m).exists():
            return True
    return False

def is_manuscript_root(p: Path) -> bool:
    # Strong signals
    if "manuscript" in p.name.lower() or "paper" in p.name.lower():
        return True
    if any(p.glob("*.tex")) and (list(p.glob("*.bib")) or any((p/q).exists() for q in ["figures","figs","images","img"])):
        return True
    # Word/Docx style: docx/pdf named manuscript + many figure/supp files
    docs = list(p.glob("*manuscript*.docx")) + list(p.glob("*manuscript*.pdf")) + list(p.glob("*paper*.docx")) + list(p.glob("*paper*.pdf"))
    if docs:
        assets = 0
        for pat in ["**/Figure*.*", "**/*Supplemental*.*", "**/*Table*.*"]:
            assets += len(list(p.glob(pat)))
        if assets >= 3:
            return True
    # Overleaf common
    if (p / "main.tex").exists():
        return True
    return False

# --------------------------- Planning ----------------------------------------

def guess_category_by_rules(path: Path) -> str:
    name = path.name.lower()
    if name in AUTOFILE_CONFIG_NAMES:
        return "ignore"    
    suffix = path.suffix.lower()
    text = name

    proposal_kw = ["specific aims", "aims", "proposal", "grant", "biosketch", "narrative", "cover letter"]
    admin_kw = ["irb", "mta", "dua", "du a", "nda", "budget", "invoice", "contract", "ica", "agreement", "ethics"]
    manus_kw = ["manuscript", "paper", "ms", "draft", "submission", "rebuttal", "overleaf"]
    talk_kw = ["slides", "talk", "poster", "deck", "seminar", "colloquium", "keynote"]
    figure_kw = ["figure", "fig ", "fig_", "supplemental figure", "supp fig"]
    supp_kw   = ["supplemental", "supp", "suppl"]
    table_kw  = ["table", "supplemental table", "supp table"]

# Figures/tables/supplemental assets => manuscript

    if suffix in {".pdf",".tif",".tiff",".png",".jpg",".jpeg",".svg",".eps"}:
        if any(k in text for k in figure_kw + supp_kw + table_kw):
            return "manuscript"
    if any(k in text for k in proposal_kw):
        return "proposals"
    if any(k in text for k in admin_kw):
        return "admin"
    if suffix in CODE_EXT:
        return "code"
    if suffix in DATA_EXT:
        return "data"
    if any(k in text for k in talk_kw) or suffix in TALKS_EXT:
        return "talks"
    if any(k in text for k in manus_kw) or suffix in MANUSCRIPT_EXT:
        return "manuscript"
    if name in {".ds_store","thumbs.db"} or name.endswith("~"):
        return "ignore"
    return "unknown"

def build_llm_messages(records, include_content: bool, peek_bytes: int):
    examples = []
    for rec in records:
        d = {
            "id": rec["id"],
            "name": rec["name"],
            "ext": rec["ext"],
            "size_bytes": rec["size_bytes"],
            "parents": rec["parents"],
            "rule_guess": rec["rule_guess"],
        }
        if include_content and rec.get("text_preview"):
            d["text_preview"] = rec["text_preview"]
        examples.append(d)

    user_prompt = {
        "role": "user",
        "content": (
            "Classify the following files. Return JSONL, one object per entry.\n"
            + json.dumps(examples, ensure_ascii=False)
        ),
    }
    return [{"role":"system", "content": SYSTEM_PROMPT}, user_prompt]

def parse_assistant_jsonl(text: str):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            out.append(obj)
        except Exception:
            start = line.find("{")
            end = line.rfind("}")
            if start != -1 and end != -1 and end > start:
                snippet = line[start:end+1]
                try:
                    obj = json.loads(snippet)
                    out.append(obj)
                except Exception:
                    pass
    return out

def scan_dump(dump: Path, ignore_dirs: set[str], bundle_code: bool, bundle_manuscript: bool):
    # Identify bundle roots and collect files
    bundle_roots = []  # list of (path, category)
    for root, dirs, files in os.walk(dump):
        p = Path(root)
        # prune ignored dirs
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        # detect bundles at this level
        if bundle_code and is_code_repo_root(p):
            bundle_roots.append((p, "code"))
            dirs[:] = []  # don't descend further; we'll handle as bundle
            continue
        if bundle_manuscript and is_manuscript_root(p):
            bundle_roots.append((p, "manuscript"))
            dirs[:] = []  # treat as bundle
            continue

    # Files not inside a bundle
    files = []
    for root, dirs, fns in os.walk(dump):
        p = Path(root)
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        # skip bundle subtrees
        if any(str(p).startswith(str(br[0])) for br in bundle_roots):
            continue
        for fn in fns:
            fp = Path(root) / fn
            if fp.name.lower() in AUTOFILE_CONFIG_NAMES:
                continue
            files.append(fp)

    return bundle_roots, files

def plan_ai(dump: Path, api_base: str, model: str, batch_size: int, include_content: bool, peek_bytes: int, ignore_dirs: set[str], bundle_code: bool, bundle_manuscript: bool):
    bundle_roots, files = scan_dump(dump, ignore_dirs, bundle_code, bundle_manuscript)

    # Build per-file records for non-bundle files
    records = []
    for i, p in enumerate(sorted(files)):
        parents = list(Path(safe_relpath(p.parent, dump)).parts)
        rec = {
            "id": f"f{i}",
            "path": str(p),
            "name": p.name,
            "ext": p.suffix.lower(),
            "size_bytes": p.stat().st_size if p.exists() else 0,
            "parents": parents,
            "rule_guess": guess_category_by_rules(p),
            "text_preview": preview_text(p, 2000) if include_content and is_textlike(p) else "",
        }
        records.append(rec)

    decisions = {}

    # Call LLM in batches
    for i in range(0, len(records), batch_size):
        batch = records[i:i+batch_size]
        messages = build_llm_messages(batch, include_content, 2000)
        try:
            resp = post_chat_completion(api_base, model, messages, timeout=120)
            content = resp["choices"][0]["message"]["content"]
            objs = parse_assistant_jsonl(content)
            by_id = {o.get("id"): o for o in objs if isinstance(o, dict)}
        except Exception as e:
            print(f"[WARN] LLM call failed for batch starting {i}: {e}\nFalling back to rule-based for this batch.")
            by_id = {}

        for rec in batch:
            o = by_id.get(rec["id"])
            if not o or o.get("category") not in CATEGORY_KEYS:
                o = {"id": rec["id"], "category": rec["rule_guess"], "confidence": 0.65, "reason": "rule-based fallback", "rename": ""}
            # upgrade unknown by extension rules
            if o["category"] in {"unknown","ignore"}:
                rb = rec["rule_guess"]
                if rb in {"data","code","manuscript","talks","proposals","admin"}:
                    o["category"] = rb
                    o["reason"] = (o.get("reason","") + " | upgraded by extension rule").strip()
            decisions[rec["path"]] = o

    # For bundle roots, create decisions for all contained files as the bundle category
    bundle_items = []
    for root, cat in bundle_roots:
        for r, dirs, fns in os.walk(root):
            pr = Path(r)
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for fn in fns:
                fp = pr / fn
                decisions[str(fp)] = {"id": None, "category": cat, "confidence": 0.99, "reason": f"{cat} bundle root at {root}", "rename": ""}
                bundle_items.append(fp)

    return records, decisions, bundle_roots

# --------------------------- Applying ----------------------------------------

def apply_plan(records, decisions, dump: Path, project_dir: Path, source_label: str, move: bool, quarantine_threshold: float):
    today = _dt.date.today().strftime("%Y%m%d")
    label = source_label or "collab"

    dests = {
        "admin": project_dir / "0_admin" / f"_from_{label}_{today}",
        "proposals": project_dir / "1_proposals" / f"_from_{label}_{today}",
        "data": project_dir / "2_data" / "raw" / f"{label}_{today}",
        "code": project_dir / "3_code" / f"_from_{label}_{today}",
        "talks": project_dir / "6_talks_posters" / f"_from_{label}_{today}",
        "manuscript": project_dir / "5_manuscript" / f"_from_{label}_{today}",
        "unknown": project_dir / "0_admin" / "_intake_unsorted" / f"{label}_{today}",
        "ignore": None,
    }

    # manifests
    manifest_csv = project_dir / f"autofile_manifest_{label}_{today}.csv"
    plan_jsonl = project_dir / f"autofile_plan_{label}_{today}.jsonl"
    log_md = project_dir / "AUTOFILE_LOG.md"

    # add a run id / timestamp for this batch
    batch_id = datetime.now().isoformat(timespec="seconds")
    
    # write plan jsonl (decisions for all known paths)
    all_paths = sorted({str(Path(dump) / safe_relpath(Path(k), dump)) for k in decisions.keys()})
    with plan_jsonl.open("w", encoding="utf-8") as f:
        for path in all_paths:
            dec = decisions.get(str(Path(path)), {})
            dec["path"] = path
            f.write(json.dumps(dec, ensure_ascii=False) + "\n")

    moved = 0
    skipped = 0
    rows = []

    for path, dec in decisions.items():
        src = Path(path)
        if src.name.lower() in AUTOFILE_CONFIG_NAMES:
            skipped += 1
            continue

        cat = dec.get("category","unknown")
        conf = float(dec.get("confidence", 0) or 0)
        # quarantine
        if cat != "ignore" and conf < quarantine_threshold:
            cat = "unknown"
        if cat == "ignore":
            skipped += 1
            continue

        base = dests.get(cat) or dests["unknown"]
        rel = safe_relpath(src, dump)
        rename = (dec.get("rename") or "").strip()
        if rename:
            rename = rename.replace("/", "-").replace("\\", "-")
            rel = rel.parent / rename

        dest = base / rel
        ensure_dir(dest.parent)
        if move:
            shutil.move(str(src), str(dest))
        else:
            shutil.copy2(str(src), str(dest))
        moved += 1
        rows.append({
            "original_path": str(src),
            "new_path": str(dest),
            "category": cat,
            "confidence": conf,
            "reason": dec.get("reason",""),
            "bytes": dest.stat().st_size if dest.exists() else "",
        })

    # write manifest CSV
    # APPEND instead of overwrite; write header only if file is new

    for r in rows:
        r["batch_id"] = batch_id
        
    # use a stable field order
    FIELDS = ["batch_id","original_path","new_path","category","confidence","reason","bytes"]

    write_header = not manifest_csv.exists()
    with manifest_csv.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    
    # write summary log
    header = f"# AutoFile Intake {today} from {label}\n\n"
    header += f"Mode: {'MOVE' if move else 'COPY'}\n\n"
    header += f"Plan: `{plan_jsonl.name}`\n\n"
    header += f"Manifest: `{manifest_csv.name}`\n\n"
    header += f"Moved {moved} files; skipped {skipped} ignored items.\n\n"
    with log_md.open("a", encoding="utf-8") as f:
        f.write(header)

    # After writing manifest/logs
    for base in [v for v in dests.values() if v is not None]:
        prune_empty_children(base)

    # keep the skeleton in place (no .keep files needed)
    ensure_project_skeleton(project_dir)

    return plan_jsonl, manifest_csv, log_md

# --------------------------- Auto-intake helper -------------------------------

def load_autofile_json(p: Path) -> dict:
    for name in [".autofile.json","autofile.json","_autofile.json"]:
        cfg = p / name
        if cfg.exists():
            try:
                return json.loads(cfg.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}

# --------------------------- CLI ---------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="AutoFile — AI-assisted, bundle-aware intake for research projects.")
    sub = ap.add_subparsers(dest="cmd")

    # AI intake command
    ap.add_argument("--ai-intake", help="Path to collaborator dump to classify (folder or file)")
    ap.add_argument("--project", help="Target project folder name, e.g., '2025-CRISPR-MutSim'")
    ap.add_argument("--source", default="", help="Short label for the dump source (e.g., 'AliceLab')")
    ap.add_argument("--base", default="~/Documents", help="Base documents directory (default: ~/Documents)")
    ap.add_argument("--apply", action="store_true", help="Actually copy/move files into the project (default is dry-run to only create a plan)")
    ap.add_argument("--move", action="store_true", help="Move files instead of copying (destructive)")

    ap.add_argument("--api-base", default=os.environ.get("LMSTUDIO_API_BASE", DEFAULT_API_BASE), help="OpenAI-style API base, e.g., http://127.0.0.1:1234/v1")
    ap.add_argument("--model", default=os.environ.get("LMSTUDIO_MODEL", DEFAULT_MODEL), help="Model name as exposed by LM Studio")
    ap.add_argument("--batch-size", type=int, default=40, help="Files per LLM call (reduce if you see OOM/timeouts)")
    ap.add_argument("--peek-bytes", type=int, default=2000, help="Max text bytes per file to send")
    ap.add_argument("--no-content", action="store_true", help="Do NOT send any file contents to the model (metadata only)")

    ap.add_argument("--bundle", default="code,manuscript", help="Comma list: code,manuscript,none")
    ap.add_argument("--ignore-dirs", default=",".join(sorted(DEFAULT_IGNORE_DIRS)), help="Comma list of directories to skip")
    ap.add_argument("--quarantine-threshold", type=float, default=0.45, help="Confidence threshold to send to quarantine")

    # Auto-intake mode (reads .autofile.json inside the drop)
    ap.add_argument("--auto-intake", help="Path to a dropped folder that includes .autofile.json with project/source/etc.")

    args = ap.parse_args()

    # Determine mode
    if args.auto_intake:
        drop = Path(os.path.expanduser(args.auto_intake)).resolve()
        if not drop.exists():
            raise SystemExit(f"Auto-intake path not found: {drop}")
        cfg = load_autofile_json(drop)
        project = cfg.get("project") or args.project or os.environ.get("AUTOFILE_DEFAULT_PROJECT")
        if not project:
            raise SystemExit("Auto-intake requires 'project' specified in .autofile.json or --project or AUTOFILE_DEFAULT_PROJECT.")
        source = cfg.get("source") or args.source or "collab"
        apply_flag = cfg.get("apply", False) or args.apply
        move_flag = cfg.get("move", False) or args.move
        use_ai = cfg.get("use_ai", True)
        bundle_list = cfg.get("bundle", [])
        bundle_code = ("code" in bundle_list) if bundle_list else ("code" in (args.bundle or ""))
        bundle_manuscript = ("manuscript" in bundle_list) if bundle_list else ("manuscript" in (args.bundle or ""))
        quarantine = float(cfg.get("quarantine_threshold", args.quarantine_threshold))

        base_dir = Path(os.path.expanduser(args.base))
        project_dir = base_dir / "Research" / "Projects" / project
        if not project_dir.exists():
            raise SystemExit(f"Project not found: {project_dir}")

        ignore_dirs = set((cfg.get("ignore_dirs") or "").split(",")) if cfg.get("ignore_dirs") else set(args.ignore_dirs.split(","))
        ignore_dirs = {d for d in ignore_dirs if d}

        if use_ai:
            records, decisions, bundle_roots = plan_ai(drop, args.api_base, args.model, args.batch_size, not args.no_content, args.peek_bytes, ignore_dirs, bundle_code, bundle_manuscript)
        else:
            # Rule-only plan
            _, files = scan_dump(drop, ignore_dirs, bundle_code, bundle_manuscript)
            records = []
            decisions = {}
            for p in files:
                cat = guess_category_by_rules(p)
                decisions[str(p)] = {"id": None, "category": cat, "confidence": 0.5, "reason": "rule-based only", "rename": ""}
            bundle_roots = []

        # Always write a plan jsonl (dry-run output)
        today = _dt.date.today().strftime("%Y%m%d")
        plan_jsonl = project_dir / f"autofile_plan_{source}_{today}.jsonl"
        with plan_jsonl.open("w", encoding="utf-8") as f:
            for path, dec in decisions.items():
                o = dict(dec)
                o["path"] = path
                f.write(json.dumps(o, ensure_ascii=False) + "\n")
        print(f"[AutoFile] Plan written: {plan_jsonl}")
        if apply_flag:
            print("[AutoFile] Applying...")
            plan_jsonl, manifest_csv, log_md = apply_plan(records, decisions, drop, project_dir, source, move_flag, quarantine)
            print(f"Applied. Manifest: {manifest_csv}")
            print(f"Summary: {log_md}")
        else:
            print("[AutoFile] Dry run only (set 'apply': true in .autofile.json or pass --apply).")
        return

    # Standard AI intake path via flags
    if not args.ai_intake or not args.project:
        ap.print_help()
        print("\nExample:\n  python autofile.py --ai-intake \"/path/to/dump\" --project \"2025-CRISPR-MutSim\" --source \"AliceLab\"")
        sys.exit(0)

    dump = Path(os.path.expanduser(args.ai_intake)).resolve()
    if not dump.exists():
        raise SystemExit(f"Dump path not found: {dump}")

    base_dir = Path(os.path.expanduser(args.base))
    project_dir = base_dir / "Research" / "Projects" / args.project
    if not project_dir.exists():
        raise SystemExit(f"Project not found: {project_dir}")

    ignore_dirs = set(args.ignore_dirs.split(",")) if args.ignore_dirs else set()
    bundle_code = "code" in (args.bundle or "")
    bundle_manuscript = "manuscript" in (args.bundle or "")

    print(f"[AutoFile] Planning intake for {dump} into {project_dir.name} using {args.model} @ {args.api_base}")
    records, decisions, bundle_roots = plan_ai(
        dump, args.api_base, args.model, args.batch_size, not args.no_content, args.peek_bytes, ignore_dirs, bundle_code, bundle_manuscript
    )

    today = _dt.date.today().strftime("%Y%m%d")
    plan_jsonl = project_dir / f"autofile_plan_{args.source or 'collab'}_{today}.jsonl"
    with plan_jsonl.open("w", encoding="utf-8") as f:
        for path, dec in decisions.items():
            o = dict(dec)
            o["path"] = path
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(f"[AutoFile] Wrote plan: {plan_jsonl}")
    print("[AutoFile] Dry run only. Use --apply to execute the plan.")

    if args.apply:
        print("[AutoFile] Applying...")
        plan_jsonl, manifest_csv, log_md = apply_plan(records, decisions, dump, project_dir, args.source or "collab", move=args.move, quarantine_threshold=args.quarantine_threshold)
        print(f"Applied. Manifest: {manifest_csv}")
        print(f"Summary: {log_md}")

if __name__ == "__main__":
    main()
    