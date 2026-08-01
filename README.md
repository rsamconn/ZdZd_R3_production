# ZdZd_R3_production

Production tracking for ZdZd → 4l Run 3 background Ntuples (mc23, p7266 DAOD_PHYS).

**Status page:** <https://rsamconn.github.io/ZdZd_R3_production/>

The tracker is a set of per-process CSVs in `data/` (one per physics process,
listed in `data/manifest.json`). `index.html` is a static status page that
renders them, served via GitHub Pages. This repo is the single source of truth —
update the CSVs (normally via `update_tracker.py`), commit, push.

**Remotes:** GitLab (<https://gitlab.cern.ch/connell/zdzd_r3_production>) is
primary — push production updates there. GitHub
(<https://github.com/rsamconn/ZdZd_R3_production>) is a secondary mirror,
updated less frequently; it's kept around because GitHub Pages is still the
main way people view progress, and the Google Sheet mirror imports from it
(see below), so don't let it drift too far behind. The default branch on both
is `master`.

## Workflow (on lxplus)

```bash
# once:
git clone https://gitlab.cern.ch/connell/zdzd_r3_production.git
# add the GitHub mirror as a second remote:
git remote add github https://github.com/rsamconn/ZdZd_R3_production.git

# 1. After grid submission (task ID printed by the submit script):
python3 update_tracker.py submit --dsid 601634 --campaign mc23d \
    --task-id 45123678 --output-dataset user.<user>.601634.mc23d.p7266.v1 \
    --code-dir /path/to/ZdZd13TeV  # -> ZdZd13TeV_commit via `git rev-parse`
# (or set $ZDZD13TEV_DIR once instead of --code-dir, or pass --commit directly)

# 2. After rucio download:
python3 update_tracker.py download --dsid 601634 --campaign mc23d \
    --dir /eos/user/<u>/<user>/dl/601634_mc23d

# 3. After merging:
python3 update_tracker.py merge --dsid 601634 --campaign mc23d \
    --merged-file /eos/user/<u>/<user>/ntuples/p7266/601634_mc23d.root
    # events + branch-entry counts via uproot if available, else add --events N

# Edit any specific cell(s) of one row (row by DSID+campaign, or full --did):
python3 update_tracker.py set \
    --did mc23_13p6TeV:mc23_13p6TeV.701185.Sh_2214_llll_m4l100_300_filt100_170.deriv.DAOD_PHYS.e8543_s4159_r15224_p7266 \
    --set "Notes=re-downloaded lost files" --set "Ntuple_files=97"

# Publish (every time, to GitLab):
git add data/ && git commit -m "601634 mc23d merged" && git push

# Publish to GitHub (periodically, to refresh the status page / Sheet mirror):
git push github master
```

The script is stdlib-only (uproot optional). Rows are keyed by DSID +
MC campaign (or the full DID with `set --did`). Manual edits to the CSVs (or
via the GitHub/GitLab web editor) are fine — just keep the header row intact.

The three stages never *downgrade* Status (e.g. running `download` on a row
already at Merged keeps Merged); pass `--status <value>` on any subcommand to
force a specific value.

## Merged-file naming convention

Merged Background Ntuples live in `/eos/.../hlrs/ZdZd/ZdZd13TeV_Ntuples/bkg_Ntuples/mc23_<Physics_process_short>/`
and are named

```
<DSID>.<Physics_identifier>.<campaign>.<ptag>.<vtag>.root
```

e.g. `701185.Sh_2214_llll_m4l100_300_filt100_170.mc23d.p7266.v1.root`

`<ptag>` is the derivation p-tag (last field of the row's `Tags` column).

Fields are dot-separated (the identifier itself contains underscores). `<vtag>`
is the Ntuple production version — bump it when the ZdZd13TeV code changes
meaningfully; the tracker's `ZdZd13TeV_commit` column maps each vtag to the
exact commit. Get the expected name for a row from the tracker itself:

```bash
python3 update_tracker.py name --dsid 701185 --campaign mc23d --vtag v1
```

The `merge` stage prints a warning if `--merged-file` doesn't match the row's
expected pattern.

## Status page (GitHub Pages)

Live at <https://rsamconn.github.io/ZdZd_R3_production/> — updates
automatically ~a minute after each push to `master` on GitHub (so it lags
behind GitLab until the periodic `git push github master`).

Preview locally: `python3 -m http.server` in the repo root, then open
<http://localhost:8000> (opening `index.html` directly won't work — `fetch` of
the CSVs needs a server).

## Google Sheet mirror (optional, read-only)

In a Google Sheet, one tab per process:

```
=IMPORTDATA("https://raw.githubusercontent.com/rsamconn/ZdZd_R3_production/master/data/H_ZZ_4l.csv")
```

Google refreshes IMPORTDATA roughly hourly. This mirror is a convenience view;
the repo remains canonical (edits made in the Sheet are NOT synced back).

## Layout

```
data/*.csv          one CSV per physics process (canonical data)
data/manifest.json  process list + page title
update_tracker.py   updater (submit / download / merge / set)
index.html          static status page (GitHub Pages)
```

## Columns — what they mean and every way to update them

Any column can additionally be edited by hand (CSV/web editor) or via
`update_tracker.py set --set "Column=Value"`; the table lists the
script-driven routes.

| Column | Meaning | Auto | Manual |
|---|---|---|---|
| DID … Events [k] | input DAOD_PHYS dataset details (p7266) | initial import | `set` only |
| JediTask_ID | PanDA JEDI task ID | — | `submit --task-id` (required) |
| Job_link | BigPanDA URL | from JediTask_ID at `submit` | `set` |
| Status | Not submitted → Submitted → Running → Finished / Failed → Downloaded → Merged → Done | `submit`/`download`/`merge` set Submitted/Downloaded/Merged, never downgrading | `--status <value>` on any subcommand (always wins) |
| ZdZd13TeV_commit | ZdZd13TeV commit hash used | `git rev-parse --short=12 HEAD` in `submit --code-dir` (or `$ZDZD13TEV_DIR`) | `submit --commit` |
| Athena_release | asetup release | defaults to `AthAnalysis,25.2.102` at `submit` (message printed) | `submit --ath-release` |
| Submitted_by | who submitted | `$USER` at `submit` | `submit --user` |
| Submitted_date | when submitted | today at `submit` | `submit --date` |
| Finished_date | when downloaded | today at `download` | `download --date` |
| Output_dataset | grid output container | — | `submit --output-dataset` |
| Ntuple_files | # .root files downloaded | counted under `download --dir` | `set` |
| Ntuple_size [GB] | their total size | summed under `download --dir` | `set` |
| Ntuple_events | events in merged file (exact count) | uproot count at `merge` | `merge --events` |
| hard_l_pdgId, truth_llll_tlv_pt, llll_tlv_pt | stored values in these branches, flattened over vectors — same count as `TTree::Draw`'s `htemp->GetEntries()` ("missing" if absent) | uproot at `merge` (tree: the file's only one, else `Nominal/llllTree`, else `--tree`) | `set` |
| Merged_file_path | final merged .root on EOS | abspath of `merge --merged-file` | `set` |
| Notes | anything | — | `set --set "Notes=..."` |

"Running/Finished/Failed/Done" are set by hand (`--status` or `set`, or by a
future PanDA-polling script) — the stages only set Submitted / Downloaded /
Merged.
