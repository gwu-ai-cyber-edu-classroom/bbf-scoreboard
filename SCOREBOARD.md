# BBF Day Scoreboard

_Last updated: <span class="local-time" data-utc="2026-06-03T10:52:54Z">2026-06-03 10:52 UTC</span>_

Ranked by overall **Score** (accumulated). Counts are **confirmed** breaks only — an issue scores once a `/repro-confirmed` comment adds the `valid` label. Column definitions are below the table.

| Rank | Team | Score | Build | Breaks landed | Breaks received | Pending | High-sev received | Fixed |
|---:|---|---:|---|---:|---:|---:|---:|---:|
| 1 | theshizaali | **10** | success | 1 | 0 | 1 | 0 | 0 |
| 2 | adamaviv | **-5** | success | 0 | 1 | 0 | 0 | 0 |

## What the columns mean

- **Score** — overall accumulated points: **+10** per break landed, **+5** extra per high-severity break landed, **+5** per fix, **-5** per break received. (Weights are tunable in `update_scoreboard.py`.)
- **Build** — status of the team's latest `build-check.yml` run (`success` / `failure` / `no-runs`).
- **Breaks landed** — confirmed breaks this team filed against *other* teams (offense).
- **Breaks received** — confirmed breaks *other* teams filed against this team's app (defense).
- **Pending** — breaks filed against this team but not yet `/repro-confirmed` (not yet scored).
- **High-sev received** — of the breaks received, how many were self-rated high severity.
- **Fixed** — received breaks closed by a merged PR (issue labeled `fixed`).

## Acting on a break (issue comments)

Put the command on the **first line** of a comment on the Break Report issue:

- `/repro-confirmed` — *(the targeted team)* you reproduced it against your running app. Applies the `valid` label and it scores. Add a line on what you observed.
- `/repro-failed` — *(the targeted team)* you could **not** reproduce it. Applies `invalid`; say what you tried so the breaker can clarify.
- `/out-of-scope` — *(facilitators only)* rule a break invalid (unsafe content, off-protocol).

Fixes: open a PR whose body says `closes #N`; when it merges, the issue is auto-labeled `fixed`.

## Pending (filed, not yet `/repro-confirmed`)

- gwu-ai-cyber-edu-classroom/bbf-build-target-theshizaali#1 by `adamaviv` — needs `/repro-confirmed` to count
