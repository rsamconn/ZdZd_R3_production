#!/usr/bin/env python3
"""
update_tracker.py -- update the mc23 p7266 Ntuple production tracker (CSV backend).

The tracker is one CSV per physics process in data/ (see data/manifest.json).
The script ONLY edits the CSVs (plus local inspection of folders/files you
point it at); it never runs rucio/panda commands itself. Stdlib-only
(uproot optional, for the merge stage).

Subcommands
-----------
  submit     after grid submission
  download   after `rucio download`
  merge      after merging the .root files
  set        edit arbitrary cells of one row directly
  name       print the expected merged-file name for a row (edits nothing)

Rows are identified by --dsid + --campaign, or (set only, also) by the full
dataset name via --did.

Status is never DOWNGRADED by the stages: e.g. running `download` on a row
already at Merged leaves the status alone (a note is printed). An explicit
--status <value> on any subcommand always wins.

How each column gets filled
---------------------------
  DID ... Events [k]    fixed input-dataset info; edit only via `set` if a
                        dataset is replaced
  JediTask_ID           `submit --task-id`
  Job_link              auto from JediTask_ID (bigpanda.cern.ch/task/<ID>/)
  Status                auto per stage (Submitted / Downloaded / Merged),
                        never downgraded; manual via --status on any
                        subcommand or `set --set "Status=..."`
  ZdZd13TeV_commit      manual via `submit --commit`, else auto
                        `git rev-parse --short=12 HEAD` in --code-dir
                        (default $ZDZD13TEV_DIR)
  Athena_release        manual via `submit --ath-release`, else defaults to
                        "AthAnalysis,25.2.102" (a message is printed)
  Submitted_by          manual via `submit --user`, else $USER
  Submitted_date        manual via `submit --date`, else today
  Finished_date         manual via `download --date`, else today
  Output_dataset        `submit --output-dataset`
  Ntuple_files          auto: count of .root* files under `download --dir`
  Ntuple_size [GB]      auto: byte sum of those files / 1e9
  Ntuple_events         auto: uproot entry count of `merge --merged-file`,
                        else `merge --events`
  hard_l_pdgId          auto at merge (uproot): total stored values in the
  truth_llll_tlv_pt       branch, flattened over vectors -- the same count as
  llll_tlv_pt             TTree::Draw("branch"); htemp->GetEntries().
                          "missing" if the branch is absent; manual via `set`
                          if uproot is unavailable
  Merged_file_path      auto: abspath of `merge --merged-file`
  Notes                 manual via `set --set "Notes=..."` (or any column:
                        `set --set "Column=Value"`, repeatable)

Examples
--------
  python3 update_tracker.py submit --dsid 601634 --campaign mc23d \\
      --task-id 45123678 --output-dataset user.rconn.601634.mc23d.p7266.v1 \\
      --code-dir ~/ZdZd13TeV

  python3 update_tracker.py download --dsid 601634 --campaign mc23d \\
      --dir /eos/user/r/rconn/dl/601634_mc23d

  python3 update_tracker.py merge --dsid 601634 --campaign mc23d \\
      --merged-file /eos/user/r/rconn/ntuples/p7266/601634_mc23d.root

  python3 update_tracker.py set \\
      --did mc23_13p6TeV:mc23_13p6TeV.701185.Sh_2214_llll_m4l100_300_filt100_170.deriv.DAOD_PHYS.e8543_s4159_r15224_p7266 \\
      --set "Notes=two files lost on site, re-downloaded" --set "Ntuple_files=97"

  python3 update_tracker.py name --dsid 701185 --campaign mc23d --vtag v1
  # -> 701185.Sh_2214_llll_m4l100_300_filt100_170.mc23d.p7266.v1.root

Merged files follow the convention
  <DSID>.<Physics_identifier>.<campaign>.<ptag>.<vtag>.root
(<ptag> is taken from the last field of the row's Tags column)
in /eos/.../bkg_Ntuples/mc23_<Physics_process_short>/ ; the merge stage warns
if --merged-file does not match the row's expected pattern.

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
DEFAULT_ATH_RELEASE = "AthAnalysis,25.2.102"
CODE_DIR_ENV = "ZDZD13TEV_DIR"
BRANCH_COLS = ["hard_l_pdgId", "truth_llll_tlv_pt", "llll_tlv_pt"]
DEFAULT_TREE = "Nominal/llllTree"

STATUS_RANK = {"Not submitted": 0, "Submitted": 1, "Running": 2, "Finished": 3,
               "Failed": 3, "Downloaded": 4, "Merged": 5, "Done": 6}


def load_all(data_dir):
    out = {}
    for path in sorted(glob.glob(os.path.join(data_dir, "*.csv"))):
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            out[path] = (reader.fieldnames, list(reader))
    if not out:
        sys.exit(f"No CSVs found in {data_dir}")
    return out


def find_row(tables, dsid=None, campaign=None, did=None):
    """Return (csv_path, row_dict). Match by full DID, or by DSID + campaign."""
    hits = []
    for path, (_fields, rows) in tables.items():
        for row in rows:
            if did is not None:
                if row["DID"] == did:
                    hits.append((path, row))
            elif str(row["DSID"]) == str(dsid) and row["MC_campaign"] == campaign:
                hits.append((path, row))
    key = did or f"DSID={dsid}, campaign={campaign}"
    if not hits:
        sys.exit(f"No row found for {key}")
    if len(hits) > 1:
        where = ", ".join(os.path.basename(p) for p, _ in hits)
        sys.exit(f"Ambiguous: {key} matches rows in {where}")
    return hits[0]


def save(path, fields, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def set_fields(row, path, fields):
    for key, val in fields.items():
        if val is not None:
            row[key] = val
            print(f"  {os.path.basename(path)}: {key} = {val}")


def apply_status(row, path, auto_status, user_status):
    """Stage statuses never downgrade; an explicit --status always wins."""
    if user_status is not None:
        set_fields(row, path, {"Status": user_status})
        return
    current = row.get("Status") or "Not submitted"
    if STATUS_RANK.get(auto_status, 0) > STATUS_RANK.get(current, 0):
        set_fields(row, path, {"Status": auto_status})
    else:
        print(f"  Status kept at '{current}' (not downgraded to '{auto_status}';"
              f" use --status to force)")


def today():
    return datetime.date.today().isoformat()


def zdzd_commit(code_dir):
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=code_dir, text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError):
        return None


# ---------------------------------------------------------------- stages ----

def stage_submit(tables, args):
    path, row = find_row(tables, args.dsid, args.campaign)
    commit = args.commit
    if commit is None:
        code_dir = args.code_dir or os.environ.get(CODE_DIR_ENV)
        if code_dir:
            commit = zdzd_commit(code_dir)
            if commit is None:
                print(f"WARNING: could not run `git rev-parse` in {code_dir};"
                      " leaving ZdZd13TeV_commit blank", file=sys.stderr)
        else:
            print("WARNING: no --commit / --code-dir / $" + CODE_DIR_ENV +
                  "; leaving ZdZd13TeV_commit blank", file=sys.stderr)
    ath_release = args.ath_release
    if ath_release is None:
        ath_release = DEFAULT_ATH_RELEASE
        print(f"Athena_release not given; defaulting to {ath_release}")
    set_fields(row, path, {
        "JediTask_ID": args.task_id,
        "Job_link": BIGPANDA.format(args.task_id),
        "ZdZd13TeV_commit": commit,
        "Athena_release": ath_release,
        "Submitted_by": args.user or getpass.getuser(),
        "Submitted_date": args.date or today(),
        "Output_dataset": args.output_dataset,
    })
    apply_status(row, path, "Submitted", args.status)
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
    set_fields(row, path, {
        "Finished_date": args.date or today(),
        "Ntuple_files": n,
        "Ntuple_size [GB]": round(total_bytes / 1e9, 3),
    })
    apply_status(row, path, "Downloaded", args.status)
    return path


def branch_draw_entries(tree, branch):
    """Total number of stored values in a branch, flattened over any vectors —
    the same count TTree::Draw("branch") reports via htemp->GetEntries()."""
    if branch not in tree:
        return "missing"
    arr = tree[branch].array(library="np")
    if arr.dtype == object:  # jagged: one histogram entry per element
        return int(sum(len(x) for x in arr))
    return int(arr.size)


def inspect_merged(path, tree_name):
    """Return (events, {branch: Draw-style entries or 'missing'}) via uproot,
    or (None, {}) if uproot is unavailable."""
    try:
        import uproot
    except ImportError:
        return None, {}
    with uproot.open(path) as f:
        if tree_name:
            tree = f[tree_name]
        else:
            trees = sorted({k.split(";")[0] for k, v in f.classnames().items()
                            if v.startswith("TTree")})
            if len(trees) == 1:
                tree = f[trees[0]]
            elif DEFAULT_TREE in trees:
                print(f"Multiple trees found; using '{DEFAULT_TREE}'"
                      " (override with --tree)")
                tree = f[DEFAULT_TREE]
            else:
                sys.exit(f"Found trees {trees} in {path}; pick one with --tree")
        branches = {b: branch_draw_entries(tree, b) for b in BRANCH_COLS}
        return tree.num_entries, branches


def stage_merge(tables, args):
    path, row = find_row(tables, args.dsid, args.campaign)
    merged = os.path.abspath(args.merged_file)
    if not os.path.isfile(merged):
        sys.exit(f"Not a file: {merged}")
    expected_prefix = (f"{row['DSID']}.{row['Physics_identifier']}"
                       f".{row['MC_campaign']}.{ptag(row)}.")
    base = os.path.basename(merged)
    if not (base.startswith(expected_prefix) and base.endswith(".root")):
        print(f"WARNING: '{base}' does not follow the naming convention"
              f" '{expected_prefix}<vtag>.root' (run the `name` subcommand"
              " for the expected name)", file=sys.stderr)
    events, branches = inspect_merged(merged, args.tree)
    if events is None:
        print("WARNING: uproot not installed; branch-entry columns left blank"
              + ("" if args.events is not None else
                 " and Ntuple_events blank (or pass --events)"), file=sys.stderr)
    if args.events is not None:
        events = args.events
    set_fields(row, path, {
        "Merged_file_path": merged,
        "Ntuple_events": int(events) if events is not None else None,
        **{b: branches.get(b) for b in BRANCH_COLS},
    })
    apply_status(row, path, "Merged", args.status)
    return path


def ptag(row):
    """Derivation p-tag: last field of the Tags column (e.g. ..._p7266 -> p7266)."""
    return row["Tags"].split("_")[-1]


def merged_name(row, vtag):
    """Expected merged-file name for a row (naming convention)."""
    return (f"{row['DSID']}.{row['Physics_identifier']}"
            f".{row['MC_campaign']}.{ptag(row)}.{vtag}.root")


def stage_name(tables, args):
    path, row = find_row(tables, args.dsid, args.campaign, args.did)
    print(merged_name(row, args.vtag))
    return None  # nothing to save


def stage_set(tables, args):
    if args.did is None and (args.dsid is None or args.campaign is None):
        sys.exit("set: give either --did, or both --dsid and --campaign")
    path, row = find_row(tables, args.dsid, args.campaign, args.did)
    fields = tables[path][0]
    updates = {}
    for item in args.set:
        if "=" not in item:
            sys.exit(f"Bad --set '{item}': expected \"Column=Value\"")
        col, val = item.split("=", 1)
        if col not in fields:
            sys.exit(f"Unknown column '{col}'. Valid columns:\n  " + "\n  ".join(fields))
        updates[col] = val
    if args.status is not None:
        updates["Status"] = args.status
    set_fields(row, path, updates)
    return path


# ------------------------------------------------------------------ main ----

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="stage", required=True)

    def common(sp, need_key=True):
        sp.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="folder with the tracker CSVs (default: data/ next to this script)")
        sp.add_argument("--dsid", required=need_key)
        sp.add_argument("--campaign", required=need_key,
                        choices=["mc23a", "mc23c", "mc23d", "mc23e"])
        sp.add_argument("--status", choices=list(STATUS_RANK),
                        help="force Status to this value (overrides the stage default "
                             "and the no-downgrade rule)")

    s = sub.add_parser("submit", help="stage 1: record grid submission")
    common(s)
    s.add_argument("--task-id", required=True, help="PanDA JEDI task ID")
    s.add_argument("--commit", help="ZdZd13TeV commit hash; default: git rev-parse in --code-dir")
    s.add_argument("--code-dir", help=f"ZdZd13TeV checkout (default ${CODE_DIR_ENV}) "
                                      "used to auto-read the commit hash")
    s.add_argument("--ath-release", help=f"release; defaults to {DEFAULT_ATH_RELEASE}")
    s.add_argument("--output-dataset", help="grid output container name")
    s.add_argument("--user", help="submitter; default $USER")
    s.add_argument("--date", help="submission date (YYYY-MM-DD); default today")

    d = sub.add_parser("download", help="stage 2: record rucio download")
    common(d)
    d.add_argument("--dir", required=True, help="folder the output was downloaded into")
    d.add_argument("--date", help="finished date (YYYY-MM-DD); default today")

    m = sub.add_parser("merge", help="stage 3: record merged Ntuple")
    common(m)
    m.add_argument("--merged-file", required=True, help="path to merged .root file")
    m.add_argument("--tree", help="TTree path, e.g. 'Nominal/llllTree' (default: "
                                  f"the only tree in the file, else '{DEFAULT_TREE}')")
    m.add_argument("--events", type=int, help="total events, overrides/replaces uproot count")

    e = sub.add_parser("set", help="edit specific cells of one row")
    common(e, need_key=False)
    e.add_argument("--did", help="full dataset name (alternative to --dsid/--campaign)")
    e.add_argument("--set", action="append", required=True, metavar='"Column=Value"',
                   help="cell to set; repeatable")

    n = sub.add_parser("name", help="print the expected merged-file name (edits nothing)")
    common(n, need_key=False)
    n.add_argument("--did", help="full dataset name (alternative to --dsid/--campaign)")
    n.add_argument("--vtag", default="v1", help="Ntuple production version (default v1)")

    args = p.parse_args()
    if args.stage in ("set", "name") and args.did is None and (
            args.dsid is None or args.campaign is None):
        sys.exit(f"{args.stage}: give either --did, or both --dsid and --campaign")
    tables = load_all(args.data_dir)
    fn = {"submit": stage_submit, "download": stage_download,
          "merge": stage_merge, "set": stage_set, "name": stage_name}
    changed = fn[args.stage](tables, args)
    if changed is None:
        return
    fields, rows = tables[changed]
    save(changed, fields, rows)
    print(f"Saved {changed}")
    print("Remember:  git add data/ && git commit && git push")


if __name__ == "__main__":
    main()
