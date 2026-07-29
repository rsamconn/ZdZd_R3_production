#!/usr/bin/env python3
"""
update_tracker.py -- update the mc23 p7266 Ntuple production tracker (CSV backend).

The tracker is one CSV per physics process in data/ (see data/manifest.json).
Three stages, matching the production workflow. The script ONLY edits the CSVs
(plus local inspection of folders/files you point it at); it never runs
rucio/panda commands itself. Stdlib-only: no pip installs needed on lxplus
(uproot optional, for event counting at the merge stage).

  1) submit    after grid submission        -> Job_ID, Job_link, Status=Submitted,
                                               Git_tag, AB_release, Submitted_by,
                                               Submitted_date, Output_dataset
  2) download  after `rucio download`       -> Finished_date, Ntuple_files,
                                               Ntuple_size [GB], Status=Downloaded
  3) merge     after merging .root files    -> Merged_file_path, Ntuple_events [k],
                                               Status=Merged

Rows are identified by DSID + MC campaign (unique across the tracker).

Examples
--------
  python3 update_tracker.py submit --dsid 601634 --campaign mc23d \
      --job-id 45123678 --output-dataset user.rconn.601634.mc23d.p7266.v1
      # --git-tag defaults to `git describe` of this repo's checkout dir if
      #   --code-dir is given; AB_release from $AnalysisBase_VERSION;
      #   submitter from $USER; dates today. All overridable.

  python3 update_tracker.py download --dsid 601634 --campaign mc23d \
      --dir /eos/user/r/rconn/dl/601634_mc23d

  python3 update_tracker.py merge --dsid 601634 --campaign mc23d \
      --merged-file /eos/user/r/rconn/ntuples/p7266/601634_mc23d.root \
      [--tree analysis] [--events 400000]

Then:  git add data/ && git commit -m "601634 mc23d submitted" && git push
"""

import argparse
import csv
import datetime
import getpass
import glob
import os
import subprocess
import sys

BIGPANDA = "https://bigpanda.cern.ch/task/{}/"
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Column names (must match the CSV headers)
C_DSID = "DSID"
C_CAMPAIGN = "MC_campaign"


def load_all(data_dir):
    """Return {csv_path: (fieldnames, rows)} for every process CSV."""
    out = {}
    for path in sorted(glob.glob(os.path.join(data_dir, "*.csv"))):
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            out[path] = (reader.fieldnames, list(reader))
    if not out:
        sys.exit(f"No CSVs found in {data_dir}")
    return out


def find_row(tables, dsid, campaign):
    """Return (csv_path, row_dict) for the row matching DSID + campaign."""
    hits = []
    for path, (_fields, rows) in tables.items():
        for row in rows:
            if str(row[C_DSID]) == str(dsid) and row[C_CAMPAIGN] == campaign:
                hits.append((path, row))
    if not hits:
        sys.exit(f"No row found for DSID={dsid}, campaign={campaign}")
    if len(hits) > 1:
        where = ", ".join(os.path.basename(p) for p, _ in hits)
        sys.exit(f"Ambiguous: DSID={dsid}, campaign={campaign} matches rows in {where}")
    return hits[0]


def save(path, fields, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def set_fields(row, path, **kv):
    for key, val in kv.items():
        key = key.replace("__", " ")  # Ntuple_size__[GB] -> "Ntuple_size [GB]"
        if val is not None:
            row[key] = val
            print(f"  {os.path.basename(path)}: {key} = {val}")


def today():
    return datetime.date.today().isoformat()


def git_describe(code_dir):
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=code_dir, text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


# ---------------------------------------------------------------- stages ----

def stage_submit(tables, args):
    path, row = find_row(tables, args.dsid, args.campaign)
    git_tag = args.git_tag or (git_describe(args.code_dir) if args.code_dir else None)
    if git_tag is None:
        print("WARNING: no --git-tag / --code-dir given; leaving Git_tag blank",
              file=sys.stderr)
    ab = args.ab_release or os.environ.get("AnalysisBase_VERSION")
    if ab is None:
        print("WARNING: --ab-release not given and $AnalysisBase_VERSION not set;"
              " leaving AB_release blank", file=sys.stderr)
    set_fields(row, path,
               Job_ID=args.job_id,
               Job_link=BIGPANDA.format(args.job_id),
               Status="Submitted",
               Git_tag=git_tag,
               AB_release=ab,
               Submitted_by=args.user or getpass.getuser(),
               Submitted_date=args.date or today(),
               Output_dataset=args.output_dataset)
    return path


def stage_download(tables, args):
    path, row = find_row(tables, args.dsid, args.campaign)
    if not os.path.isdir(args.dir):
        sys.exit(f"Not a directory: {args.dir}")
    n, total_bytes = 0, 0
    for dirpath, _dirs, files in os.walk(args.dir):
        for f in files:
            if ".root" in f:  # matches file.root and file.root.1
                n += 1
                total_bytes += os.path.getsize(os.path.join(dirpath, f))
    if n == 0:
        sys.exit(f"No .root files found under {args.dir}")
    set_fields(row, path,
               Finished_date=args.date or today(),
               Ntuple_files=n,
               Status="Downloaded",
               **{"Ntuple_size [GB]": round(total_bytes / 1e9, 3)})
    return path


def count_events(path, tree):
    try:
        import uproot
    except ImportError:
        return None
    with uproot.open(path) as f:
        if tree:
            return f[tree].num_entries
        trees = sorted({k.split(";")[0] for k, v in f.classnames().items()
                        if v.startswith("TTree")})
        if len(trees) != 1:
            sys.exit(f"Found trees {trees} in {path}; pick one with --tree")
        return f[trees[0]].num_entries


def stage_merge(tables, args):
    path, row = find_row(tables, args.dsid, args.campaign)
    merged = os.path.abspath(args.merged_file)
    if not os.path.isfile(merged):
        sys.exit(f"Not a file: {merged}")
    events = args.events
    if events is None:
        events = count_events(merged, args.tree)
        if events is None:
            print("WARNING: uproot not installed and --events not given;"
                  " leaving Ntuple_events blank", file=sys.stderr)
    set_fields(row, path,
               Merged_file_path=merged,
               Status="Merged",
               **{"Ntuple_events [k]":
                  round(events / 1e3, 1) if events is not None else None})
    return path


# ------------------------------------------------------------------ main ----

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="stage", required=True)

    def common(sp):
        sp.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="folder with the tracker CSVs (default: data/ next to this script)")
        sp.add_argument("--dsid", required=True)
        sp.add_argument("--campaign", required=True,
                        choices=["mc23a", "mc23c", "mc23d", "mc23e"])
        sp.add_argument("--date", help="override date (YYYY-MM-DD); default today")

    s = sub.add_parser("submit", help="stage 1: record grid submission")
    common(s)
    s.add_argument("--job-id", required=True, help="PanDA JEDI task ID")
    s.add_argument("--git-tag", help="git tag/commit of the analysis code")
    s.add_argument("--code-dir", help="analysis code checkout; used for `git describe` "
                                      "when --git-tag is not given")
    s.add_argument("--ab-release", help="AnalysisBase release; default $AnalysisBase_VERSION")
    s.add_argument("--output-dataset", help="grid output container name")
    s.add_argument("--user", help="submitter; default $USER")

    d = sub.add_parser("download", help="stage 2: record rucio download")
    common(d)
    d.add_argument("--dir", required=True, help="folder the output was downloaded into")

    m = sub.add_parser("merge", help="stage 3: record merged Ntuple")
    common(m)
    m.add_argument("--merged-file", required=True, help="path to merged .root file")
    m.add_argument("--tree", help="TTree name for event counting (default: auto)")
    m.add_argument("--events", type=int, help="total events, if not using uproot")

    args = p.parse_args()
    tables = load_all(args.data_dir)
    fn = {"submit": stage_submit, "download": stage_download, "merge": stage_merge}
    changed = fn[args.stage](tables, args)
    fields, rows = tables[changed]
    save(changed, fields, rows)
    print(f"Saved {changed}")
    print("Remember:  git add data/ && git commit && git push")


if __name__ == "__main__":
    main()
