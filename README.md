# BBF Day Scoreboard

Live scoreboard for the Build-it / Break-it / Fix-it day activity.

## How it works

- `teams.yaml` lists every competing team with its `id`, display `name`, `repo` (full `org/repo` path), and `members` (GitHub logins).
- `scripts/update_scoreboard.py` reads `teams.yaml` + `settings.yaml`, queries every team's repo via the `gh` CLI, and writes four files: `SCOREBOARD.md` (the Scores table), `scores.json` (graph history), `breaks.json` (the interactive break feed), and `settings.json` (client config, including the data-update cadence read from the cron).
- `index.html` is a dark CTF board: **Scores** (always shown), then collapsible **Score-over-time graph**, **Break feed** (filter/sort by status / team / severity, with issue links), and **Columns & acting on a break** — all hidden by default, click to expand. It re-fetches on an interval when `live_updates` is on.
- `settings.yaml` controls everything (theme, which sections exist, Score weights, and the auto-update schedule). See below.
- `.github/workflows/update-scoreboard.yml` runs the updater on cron, on push to `teams.yaml` / `settings.yaml`, and on `workflow_dispatch`. `.github/workflows/apply-settings.yml` syncs the cron cadence and enables/disables the updater from `settings.yaml`.

## Settings (`settings.yaml`)

```yaml
title: "BBF Scoreboard"
schedule:
  enabled: true            # turn the auto-update workflow on/off (no manual edits)
  update_minutes: 5        # cron cadence; apply-settings.yml writes this into the cron
display:
  live_updates: true       # browser re-fetches on an interval
  refresh_seconds: 30      # browser re-fetch cadence when live
  theme: dark              # dark | light
  # Graph / Break feed / Columns / Teams are always shown as collapsible panels
  # (collapsed by default; click ▸ to expand) — not toggled in settings.
scoring:                   # overall Score weights (points)
  landed: 10
  high_sev_landed: 5
  fixed: 5
  received: -5
  duplicate_halflife_minutes: 5   # later duplicates of the same break decay
history:
  max_points: 1000         # chart length cap
```

Editing `schedule` is special: a push to `settings.yaml` runs **apply-settings.yml**, which rewrites the cron in `update-scoreboard.yml` to `*/update_minutes` and runs `gh workflow enable|disable` per `schedule.enabled` — so you never hand-edit the workflow or touch the Actions UI.

## How a break gets counted (and shown)

A filed issue does **not** score until it is confirmed, but it is always visible in the Break feed with a **State**:

- **pending** — filed, not yet `/repro-confirmed`.
- **repro** — the targeted team commented **`/repro-confirmed`**; the `issue-events` workflow added the `valid` label and it now scores.
- **fix-review** — the target merged a PR (`closes #N`) so the issue is `fix-claimed`, but the fix is **awaiting the breaker's confirmation** (it does not score the fix yet).
- **fixed** — the **breaker** (the team that filed it) re-tested and commented **`/fix-confirmed`**; the fix scores.

**Fix-review round:** a fix isn't credited on the target's say-so. After the target merges `closes #N` (→ `fix-claimed` / *fix-review*), the team that filed the break re-runs it and comments **`/fix-confirmed`** (→ `fixed`, scores) or **`/fix-failed`** (→ reopens, `fix-rejected`). Facilitators can rule a break out with **`/out-of-scope`**. The state labels are auto-created by `issue-events.yml`; the author must be in `teams.yaml` and it can't be a self-break to score.

**To confirm by hand** (e.g., testing): comment `/repro-confirmed`, or `gh issue edit <N> -R <org>/<repo> --add-label valid`.

## How Score is calculated

Each team's **Score** is the sum, over its confirmed breaks, of:

- **+`landed`** (default 10) for each confirmed break you filed against another team, **+`high_sev_landed`** (default 5) more if it was high severity;
- **+`fixed`** (default 5) for each break against you that you closed with a merged PR;
- **`received`** (default −5) for each confirmed break others landed on you.

**First finder wins, duplicates decay.** Breaks that hit the **same target with the same SPEC property and attack class** are one "group." The earliest-filed confirmed break in a group earns **full** points; a later duplicate earns `base × 0.5 ^ (minutes_after_first / duplicate_halflife_minutes)` — i.e., **halved every ~5 minutes**, decaying toward zero. The same decay reduces the target's `received` penalty for duplicates (one hole ≈ one penalty). The Break feed's **Pts** column shows what each break earned. Pending / invalid / self-authored / unattributed breaks never score. All weights (and the halflife) are tunable in `settings.yaml`.

**To confirm an issue by hand** (e.g., when testing the board): comment `/repro-confirmed` on it, or apply the label directly —

```bash
gh issue edit <issue-number> -R <org>/<repo> --add-label valid
```

Then trigger a refresh (Actions → Update Scoreboard → Run workflow) instead of waiting for the cron.

## Setup

1. **`SCOREBOARD_PAT` secret.** Create a fine-scoped Personal Access Token at <https://github.com/settings/personal-access-tokens/new> with:
   - Repository access: All repositories in the org
   - Permissions: `Contents: read & write`, `Issues: read`, `Metadata: read`, and — for `apply-settings.yml` to manage the schedule — `Actions: read & write` and `Workflows: read & write` (editing a workflow's cron requires the Workflows scope; enabling/disabling a workflow requires the Actions scope).

   Add it as `SCOREBOARD_PAT` under Settings → Secrets and variables → Actions.

2. **GitHub Pages.** Enable at Settings → Pages → Source: `Deploy from a branch`, branch `main`, folder `/`. The live URL is `https://<org>.github.io/bbf-scoreboard/`.

## Day-of

Run `gen-teams-yaml.sh <org>` from the scaffold to regenerate `teams.yaml` from the org's GitHub Teams. The on-`push` trigger refreshes the scoreboard immediately.

## Manual refresh

Actions → Update Scoreboard → Run workflow.

## Files

```
README.md                              (this file)
settings.yaml                          (board config: live updates, theme, weights, toggles)
teams.yaml                             (roster, regenerated by gen-teams-yaml.sh)
teams.yaml.example                     (example structure)
SCOREBOARD.md                          (auto-generated Scores table; do not edit)
scores.json                            (auto-generated graph history; do not edit)
breaks.json                            (auto-generated break feed; do not edit)
teams.json                             (auto-generated team roster for the Teams section; do not edit)
settings.json                          (auto-generated client subset of settings.yaml; do not edit)
index.html                             (dark CTF board: scores + graph + break feed + columns + teams)
scripts/
  update_scoreboard.py                 (the updater + validator)
.github/workflows/
  update-scoreboard.yml                (cron + push + dispatch; guarded by schedule.enabled)
  apply-settings.yml                   (syncs cron + enable/disable from settings.yaml)
```
