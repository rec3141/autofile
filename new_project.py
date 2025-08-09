#!/usr/bin/env python3
"""
new_project.py — bootstrap your research filesystem, create new projects, and ingest collaborator dumps.

Usage:
  # One-time setup (creates base folders + Project_Template)
  python new_project.py --setup --owner "Your Name"

  # Create a new project from the template (uses current year by default)
  python new_project.py "CRISPR-MutSim"
  python new_project.py "CRISPR-MutSim" --year 2025

  # Ingest a collaborator dump into an existing project (copy by default)
  python new_project.py --intake "/path/to/dump" --project "2025-CRISPR-MutSim" --source "AliceLab"
  # Move instead of copy (dangerous, faster)
  python new_project.py --intake "/path/to/dump" --project "2025-CRISPR-MutSim" --move
  # Base location for everything (defaults to ~/Documents)
  python new_project.py --intake "/dump" --project "2025-CRISPR-MutSim" --base "~/Dropbox/Documents"

Heuristics:
  - Data files (.csv, .tsv, .xlsx, .parquet, .h5, .fastq, .bam, .tif/.tiff, .nii, .hdf5, .mat, .rds)
      => 2_data/raw/<source>_<YYYYMMDD>/(preserve relative paths)
  - Code (.py, .R, .ipynb, .m, .jl, .sh, .bat, .ps1, .sql) => 3_code/_from_<source>_<YYYYMMDD>/
  - Manuscripts (names containing: 'manuscript','paper','ms','draft','submission','rebuttal' or .tex/.bib/.doc/.docx)
      => 5_manuscript/_from_<source>_<YYYYMMDD>/
  - Talks/posters (.ppt/.pptx/.key, names containing 'slides','talk','poster','deck','seminar','colloquium')
      => 6_talks_posters/_from_<source>_<YYYYMMDD>/
  - Proposals (names containing 'specific aims','aims','proposal','grant','biosketch','cover letter','narrative')
      => 1_proposals/_from_<source>_<YYYYMMDD>/
  - Admin/agreements (names containing 'irb','mta','du a','nda','budget','invoice','contract','ica','agreement')
      => 0_admin/_from_<source>_<YYYYMMDD>/
  - Unknown => 0_admin/_intake_unsorted/<source>_<YYYYMMDD>/

It writes:
  - INTAKE_LOG.md (human-readable summary; first 200 rows)
  - intake_manifest.csv (full listing)
  - For data, a README_data.md stub under the new raw/ subfolder.
"""
import argparse
import datetime as _dt
import hashlib
import os
from pathlib import Path
import shutil
import sys
import csv


BASE_FOLDERS = ["Teaching", "Research", "Service", "Personal", "Fun"]

PROJECT_SUBFOLDERS = [
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

READ_ME = """# {project_title}

**Owner:** {owner}
**Status:** {status}
**Created:** {created_date}

## Summary
<one paragraph on scope & why>

## Next 3 Actions
1. 
2. 
3. 

## Key Links
- Data dictionary: ./2_data/README_data.md
- Draft manuscript: ./5_manuscript/
- Code entrypoint: ./3_code/

## Log
- {created_date}: Project created.
"""

DATA_README = """# Data Notes

- `raw/` contains immutable original data. Do not edit in place.
- `processed/` contains derived/cleaned data that can be regenerated.
- Record data provenance below.

## Provenance
- YYYY-MM-DD: what you did, from what to what, script used.
"""

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def bootstrap(base_dir: Path, owner: str = "", quiet: bool = False):
    """Create the overall structure + a Project_Template."""
    documents = base_dir.expanduser()
    ensure_dir(documents)

    # Top-level
    for name in BASE_FOLDERS:
        ensure_dir(documents / name)

    research = documents / "Research"
    ensure_dir(research / "00_@Inbox")
    ensure_dir(research / "Projects")
    ensure_dir(research / "Outputs" / "Papers")
    ensure_dir(research / "Outputs" / "Talks")
    ensure_dir(research / "Methods")
    ensure_dir(research / "Funding")
    ensure_dir(research / "99_Archive" / "Projects")
    ensure_dir(research / "99_Archive" / "Proposals")

    # Project template
    tmpl = research / "Projects" / "Project_Template"
    for sub in PROJECT_SUBFOLDERS:
        ensure_dir(tmpl / sub)

    # README files
    created = _dt.date.today().isoformat()
    (tmpl / "README.md").write_text(
        READ_ME.format(
            project_title="Project Title",
            owner=owner or "<your name>",
            status="ACTIVE",
            created_date=created,
        ),
        encoding="utf-8",
    )
    ensure_dir(tmpl / "2_data")
    (tmpl / "2_data" / "README_data.md").write_text(DATA_README, encoding="utf-8")

    print(f"Bootstrapped structure under: {documents}")
    print(f"Template created at: {tmpl}")

def slugify(name: str) -> str:
    bad = set('/\\:*?"<>|')
    s = name.strip().replace(" ", "-")
    return "".join(ch for ch in s if ch not in bad)

def create_project(base_dir: Path, name: str, year: int | None, owner: str = "") -> Path:
    documents = base_dir.expanduser()
    research = documents / "Research"
    projects = research / "Projects"
    if not projects.exists():
        raise SystemExit("Projects directory not found. Run with --setup first.")

    year = year or _dt.date.today().year
    folder_name = f"{year}-{slugify(name)}"
    dest = projects / folder_name

    tmpl = projects / "Project_Template"
    if not tmpl.exists():
        raise SystemExit("Project_Template not found. Run with --setup first.")

    if dest.exists():
        raise SystemExit(f"Destination already exists: {dest}")

    shutil.copytree(tmpl, dest)

    # Personalize README
    readme = dest / "README.md"
    txt = readme.read_text(encoding="utf-8")
    txt = txt.replace("Project Title", name)
    txt = txt.replace("<your name>", owner or "")
    readme.write_text(txt, encoding="utf-8")

    print(f"Created new project at: {dest}")
    return dest

# === Intake helpers ===

DATA_EXT = {
    ".csv",".tsv",".xlsx",".xls",".parquet",".h5",".hdf5",".feather",".rds",".rdata",".sav",
    ".dta",".mat",".gz",".zip",".fastq",".fq",".bam",".sam",".vcf",".tif",".tiff",".nii",".nii.gz"
}
CODE_EXT = {".py",".r",".ipynb",".m",".jl",".sh",".bash",".bat",".ps1",".sql",".yaml",".yml",".toml",".json",".R"}
MANUSCRIPT_EXT = {".tex",".bib",".doc",".docx",".rtf",".odt",".pdf"}
TALKS_EXT = {".ppt",".pptx",".key",".pdf"}
IMAGE_EXT = {".png",".jpg",".jpeg",".svg",".eps",".tif",".tiff",".pdf"}

def categorize(path: Path):
    name = path.name.lower()
    stem = path.stem.lower()
    suffix = path.suffix.lower()

    text = name  # simple keyword bag
    # Proposals first (keywords override generic manus)
    proposal_kw = ["specific aims", "aims", "proposal", "grant", "biosketch", "narrative", "cover letter"]
    admin_kw = ["irb", "mta", "dua", "du a", "nda", "budget", "invoice", "contract", "ica", "agreement", "ethics"]
    manus_kw = ["manuscript", "paper", "ms", "draft", "submission", "rebuttal", "overleaf"]
    talk_kw = ["slides", "talk", "poster", "deck", "seminar", "colloquium", "keynote"]

    # Category rules
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
    # images that could be figures
    if suffix in IMAGE_EXT:
        return "manuscript"  # figures likely belong near manus/analysis; default to manuscript bucket
    return "unknown"

def safe_relpath(child: Path, parent: Path) -> Path:
    try:
        return child.relative_to(parent)
    except Exception:
        return Path(child.name)

def sha256_if_small(fp: Path, max_mb: int = 500) -> str:
    try:
        size_mb = fp.stat().st_size / (1024*1024)
        if size_mb > max_mb:
            return ""
        h = hashlib.sha256()
        with fp.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""

def intake_dump(base_dir: Path, project_name: str, dump_path: Path, source_label: str = "", move: bool = False):
    documents = base_dir.expanduser()
    projects = documents / "Research" / "Projects"
    project_dir = projects / project_name
    if not project_dir.exists():
        raise SystemExit(f"Project not found: {project_dir}")

    dump = dump_path.expanduser().resolve()
    if not dump.exists():
        raise SystemExit(f"Dump path not found: {dump}")

    today = _dt.date.today().strftime("%Y%m%d")
    label = source_label or "collab"
    # Destination bases
    dests = {
        "admin": project_dir / "0_admin" / f"_from_{label}_{today}",
        "proposals": project_dir / "1_proposals" / f"_from_{label}_{today}",
        "data": project_dir / "2_data" / "raw" / f"{label}_{today}",
        "code": project_dir / "3_code" / f"_from_{label}_{today}",
        "talks": project_dir / "6_talks_posters" / f"_from_{label}_{today}",
        "manuscript": project_dir / "5_manuscript" / f"_from_{label}_{today}",
        "unknown": project_dir / "0_admin" / "_intake_unsorted" / f"{label}_{today}",
    }
    for d in dests.values():
        ensure_dir(d)

    # Ensure data README exists under the new raw/ bucket
    data_readme = dests["data"].parent / "README_data.md"
    if not data_readme.exists():
        data_readme.write_text(
            "# Data Notes\n\nThis folder contains raw collaborator data drops. Do not edit files in place.\n",
            encoding="utf-8",
        )

    # Walk dump and place files
    manifest_rows = []
    for root, dirs, files in os.walk(dump):
        for fname in files:
            src = Path(root) / fname
            category = categorize(src)
            # Preserve relative layout under a category bucket
            rel = safe_relpath(src, dump)
            dest_base = dests.get(category, dests["unknown"])
            dest = dest_base / rel
            ensure_dir(dest.parent)

            if move:
                shutil.move(str(src), str(dest))
            else:
                # copy2 preserves mtime
                shutil.copy2(str(src), str(dest))

            size = dest.stat().st_size if dest.exists() else 0
            checksum = sha256_if_small(dest)
            manifest_rows.append({
                "original_path": str(src),
                "new_path": str(dest),
                "bytes": size,
                "sha256_if_small": checksum,
                "category": category,
            })

    # Write manifest CSV
    manifest_csv = project_dir / f"intake_manifest_{label}_{today}.csv"
    with manifest_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["original_path","new_path","bytes","sha256_if_small","category"])
        writer.writeheader()
        writer.writerows(manifest_rows)

    # Write human-readable summary
    log_md = project_dir / "INTAKE_LOG.md"
    header = f"# Intake {today} from {label}\n\n"
    header += f"Source dump: `{dump}`\n\n"
    header += f"Mode: {'MOVE' if move else 'COPY'}\n\n"
    header += "Placed into:\n" + "\n".join([f"- `{k}` -> `{v}`" for k, v in dests.items()]) + "\n\n"
    header += f"Full manifest: `{manifest_csv.name}`\n\n"
    # Preview first 200
    preview = manifest_rows[:200]
    rows = "\n".join([f"- {r['category']}: {r['original_path']}  →  {r['new_path']}" for r in preview])
    more = "" if len(manifest_rows) <= 200 else f"\n… and {len(manifest_rows)-200} more (see CSV)."
    with log_md.open("a", encoding="utf-8") as f:
        f.write(header + rows + more + "\n\n")

    print(f"Ingest complete. Summary: {log_md}")
    print(f"Manifest: {manifest_csv}")
    return log_md, manifest_csv

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Bootstrap academic research folders, create projects, and ingest collaborator dumps."
    )
    parser.add_argument("name", nargs="?", help="Project slug/name (e.g., 'CRISPR-MutSim')")
    parser.add_argument("--year", type=int, help="Year for the project folder name")
    parser.add_argument("--base", default="~/Documents", help="Base documents directory (default: ~/Documents)")
    parser.add_argument("--owner", default="", help="Default owner name stamped into README files")
    parser.add_argument("--setup", action="store_true", help="Run one-time setup and create Project_Template")
    # Intake options
    parser.add_argument("--intake", help="Path to collaborator dump to ingest into a project")
    parser.add_argument("--project", help="Target project folder name, e.g., '2025-CRISPR-MutSim'")
    parser.add_argument("--source", help="Short label for the dump source (e.g., 'AliceLab')", default="")
    parser.add_argument("--move", action="store_true", help="Move files instead of copying (faster, destructive)")

    args = parser.parse_args(argv)
    base_dir = Path(os.path.expanduser(args.base))

    # Intake flow (has its own flags)
    if args.intake:
        if not args.project:
            raise SystemExit("Please supply --project 'YYYY-ProjectSlug' for intake.")
        return intake_dump(base_dir, args.project, Path(args.intake), source_label=args.source, move=args.move)

    # Setup / create flow
    if args.setup:
        bootstrap(base_dir, owner=args.owner)
        if args.name:
            create_project(base_dir, args.name, args.year, owner=args.owner)
        return

    if not args.name:
        print("Nothing to do. Provide a project name, use --setup, or use --intake.\n"
              "Examples:\n"
              "  python new_project.py --setup --owner \"Your Name\"\n"
              "  python new_project.py \"CRISPR-MutSim\"\n"
              "  python new_project.py --intake \"/path/to/dump\" --project \"2025-CRISPR-MutSim\" --source \"AliceLab\"\n")
        return

    create_project(base_dir, args.name, args.year, owner=args.owner)

if __name__ == "__main__":
    main()
