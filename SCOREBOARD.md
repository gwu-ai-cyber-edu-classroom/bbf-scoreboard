# BBF Day Scoreboard

_Last updated: <span class="local-time" data-utc="2026-06-02T21:04:40Z">2026-06-02 21:04 UTC</span>_

Counts are **confirmed** breaks only — an issue scores once a `/repro-confirmed` comment adds the `valid` label. Column definitions are below the table.

| Team | Build | Breaks landed | Breaks received | Pending | High-sev received | Fixed |
|---|---|---:|---:|---:|---:|---:|
| theshizaali | success | 1 | 0 | 1 | 0 | 0 |
| adamaviv | success | 0 | 1 | 0 | 0 | 0 |

## What the columns mean

- **Build** — status of the team's latest `build-check.yml` run (`success` / `failure` / `no-runs`).
- **Breaks landed** — confirmed breaks this team filed against *other* teams (offense).
- **Breaks received** — confirmed breaks *other* teams filed against this team's app (defense).
- **Pending** — breaks filed against this team but not yet `/repro-confirmed` (not yet scored).
- **High-sev received** — of the breaks received, how many were self-rated high severity.
- **Fixed** — received breaks closed by a merged PR (issue labeled `fixed`).

## Pending (filed, not yet `/repro-confirmed`)

- gwu-ai-cyber-edu-classroom/bbf-build-target-theshizaali#1 by `adamaviv` — needs `/repro-confirmed` to count
