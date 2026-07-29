# ZdZd_R3_production

Production tracking for ZdZd → 4l Run 3 background Ntuples (mc23, p7266 DAOD_PHYS).

**Status page:** <https://rsamconn.github.io/ZdZd_R3_production/>

The tracker is a set of per-process CSVs in `data/` (one per physics process,
listed in `data/manifest.json`). `index.html` is a static status page that
renders them, served via GitHub Pages. This repo is the single source of truth —
update the CSVs (normally via `update_tracker.py`), commit, push.

## Workflow (on lxplus)

```bash
# once:
git clone https://github.com/rsamconn/ZdZd_R3_production.git
# 1. After grid submission (task ID printed by the submit script):
python3 update_tracker.py submit --dsid 601634 --campaign mc23d \
    --job-id 45123678 --output-dataset user.<user>.601634.mc23d.p7266.v1 \
    --code-dir /path/to/ZdZd13TeV      # -> Git_tag from `git describe`
# AB_release auto-read from $AnalysisBase_VERSION (asetup), user from $USER.

# 2. After rucio download:
python3 update_tracker.py download --dsid 601634 --campaign mc23d \
    --dir /eos/user/<u>/<user>/dl/601634_mc23d

# 3. After merging:
python3 update_tracker.py merge --dsid 601634 --campaign mc23d \
    --merged-file /eos/user/<u>/<user>/ntuples/p7266/601634_mc23d.root
    # events counted with uproot if available, else add --events N

# Publish:
git add data/ && git commit -m "601634 mc23d merged" && git push
```

The script is stdlib-only (uproot optional). Rows are keyed by DSID +
MC campaign. Manual edits to the CSVs (or via the GitHub web editor) are fine —
just keep the header row intact.

## Status page (GitHub Pages)

Live at <https://rsamconn.github.io/ZdZd_R3_production/> — updates
automatically ~a minute after each push to `main`.

Preview locally: `python3 -m http.server` in the repo root, then open
<http://localhost:8000> (opening `index.html` directly won't work — `fetch` of
the CSVs needs a server).

## Google Sheet mirror (optional, read-only)

In a Google Sheet, one tab per process:

```
=IMPORTDATA("https://raw.githubusercontent.com/rsamconn/ZdZd_R3_production/main/data/H_ZZ_4l.csv")
```

Google refreshes IMPORTDATA roughly hourly. This mirror is a convenience view;
the repo remains canonical (edits made in the Sheet are NOT synced back).

## Layout

```
data/*.csv          one CSV per physics process (canonical data)
data/manifest.json  process list + page title
update_tracker.py   3-stage updater (submit / download / merge)
index.html          static status page (GitHub Pages)
```

## Columns

| Column | Filled by | Meaning |
|---|---|---|
| DID … Events [k] | initial import | input DAOD_PHYS dataset details (p7266) |
| Job_ID / Job_link | stage 1 | PanDA JEDI task ID / BigPanDA URL |
| Status | stages 1–3 | Not submitted → Submitted → Running → Finished / Failed → Downloaded → Merged → Done |
| Git_tag / AB_release | stage 1 | analysis code `git describe` / asetup release |
| Submitted_by / dates | stages 1–2 | who + when |
| Output_dataset | stage 1 | grid output container |
| Ntuple_files / size | stage 2 | counted in the rucio download folder |
| Ntuple_events [k] | stage 3 | events in the merged file |
| Merged_file_path | stage 3 | final merged .root on EOS |

"Running/Finished/Failed/Done" are set by hand (or by a future PanDA-polling
script) — the three stages only set Submitted / Downloaded / Merged.
