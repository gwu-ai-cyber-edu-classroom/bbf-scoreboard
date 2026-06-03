#!/usr/bin/env python3
"""Update SCOREBOARD.md, scores.json, and settings.json from teams.yaml +
settings.yaml using the `gh` CLI.

Logic:
  1. Load teams.yaml + settings.yaml. Validate the roster. Exit non-zero on
     failure -- the workflow run will show red.
  2. For each team repo, list all issues with author + labels (one gh call).
  3. For each `valid`-labeled issue, attribute landed/received + severity/fixed.
  4. Compute an overall accumulated Score per team (weights from settings.yaml).
  5. Fetch the latest build-check.yml conclusion per repo.
  6. Render SCOREBOARD.md (leaderboard), append scores.json (chart history), and
     write settings.json (client subset for index.html).

Usage:
    python update_scoreboard.py [--validate-only]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Missing dependency: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
SCORES_JSON = ROOT / "scores.json"
SETTINGS_YAML = ROOT / "settings.yaml"
SETTINGS_JSON = ROOT / "settings.json"

DEFAULT_SETTINGS = {
    "title": "BBF Scoreboard",
    "display": {
        "live_updates": True,
        "refresh_seconds": 30,
        "theme": "dark",
        "show_chart": True,
        "show_pending": True,
        "show_diagnostics": True,
    },
    "scoring": {"landed": 10, "high_sev_landed": 5, "fixed": 5, "received": -5},
    "history": {"max_points": 1000},
}


def load_settings() -> dict:
    """Deep-merge settings.yaml over DEFAULT_SETTINGS (missing file == defaults)."""
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))  # deep copy
    try:
        user = yaml.safe_load(SETTINGS_YAML.read_text()) or {}
    except FileNotFoundError:
        user = {}
    for key, val in user.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key].update(val)
        else:
            merged[key] = val
    return merged


def write_settings_json(settings: dict) -> None:
    """Write the client-readable subset index.html consumes."""
    d = settings["display"]
    SETTINGS_JSON.write_text(json.dumps({
        "title": settings.get("title", "BBF Scoreboard"),
        "liveUpdates": bool(d.get("live_updates", True)),
        "refreshSeconds": int(d.get("refresh_seconds", 30)),
        "theme": "light" if str(d.get("theme")) == "light" else "dark",
        "showChart": bool(d.get("show_chart", True)),
    }, indent=2) + "\n")


def gh_json(args: list[str]) -> object:
    out = subprocess.check_output(["gh", *args], text=True)
    return json.loads(out)


def gh_check(args: list[str]) -> bool:
    return subprocess.run(["gh", *args], capture_output=True).returncode == 0


def validate(teams: list[dict]) -> list[str]:
    problems: list[str] = []
    seen_logins: dict[str, str] = {}
    seen_ids: set[str] = set()
    seen_repos: set[str] = set()
    for t in teams:
        for f in ("id", "name", "repo", "members"):
            if f not in t:
                problems.append(f"team is missing required field '{f}': {t!r}")
        tid = t.get("id")
        if tid in seen_ids:
            problems.append(f"duplicate team id: {tid}")
        if tid:
            seen_ids.add(tid)
        repo = t.get("repo")
        if repo in seen_repos:
            problems.append(f"duplicate team repo: {repo}")
        if repo:
            seen_repos.add(repo)
        members = t.get("members") or []
        if not isinstance(members, list) or not members:
            problems.append(f"team {tid} has empty or missing members list")
        for login in members:
            if login in seen_logins:
                problems.append(
                    f"login '{login}' appears in two teams: "
                    f"{seen_logins[login]} and {tid}"
                )
            else:
                seen_logins[login] = tid
        if repo and not gh_check(["repo", "view", repo]):
            problems.append(f"repo not found on GitHub: {repo}")
        for login in members:
            if not gh_check(["api", f"users/{login}"]):
                problems.append(f"login not found on GitHub: {login}")
    return problems


def fetch_issues(repo: str) -> list[dict]:
    return gh_json([
        "issue", "list", "-R", repo, "--state", "all",
        "--json", "number,author,labels,body",
        "--limit", "1000",
    ])


def fetch_build_status(repo: str) -> str:
    try:
        runs = gh_json([
            "run", "list", "-R", repo,
            "--workflow", "build-check.yml",
            "--limit", "1",
            "--json", "conclusion",
        ])
        if not runs:
            return "no-runs"
        return runs[0].get("conclusion") or "in-progress"
    except subprocess.CalledProcessError:
        return "error"


FORM_FIELD_HEADINGS = {
    "attack_class": "Attack class",
    "severity": "Severity (self-rated)",
}


def parse_form_field(body: str | None, field_id: str) -> str | None:
    if not body:
        return None
    label = FORM_FIELD_HEADINGS.get(field_id, field_id)
    pattern = re.compile(rf"^###\s+{re.escape(label)}\s*$", re.MULTILINE)
    match = pattern.search(body)
    if not match:
        return None
    for line in body[match.end():].splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def append_history(teams: list[dict], score: dict, iso: str, max_points: int) -> None:
    try:
        data = json.loads(SCORES_JSON.read_text())
        if not isinstance(data, dict):
            raise ValueError
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        data = {"teams": [], "history": []}
    data["teams"] = [{"id": t["id"], "name": t["name"]} for t in teams]
    data.setdefault("history", []).append(
        {"t": iso, "scores": {t["id"]: score.get(t["id"], 0) for t in teams}}
    )
    if max_points > 0:
        data["history"] = data["history"][-max_points:]
    SCORES_JSON.write_text(json.dumps(data, indent=2) + "\n")


def render_scoreboard(
    *, settings, teams, score, breaks_landed, breaks_received, high_sev_received,
    fixed, pending_received, build_status, unattributed, pending, self_authored,
    iso, utc_fallback,
) -> str:
    disp = settings["display"]
    w = settings["scoring"]
    show_pending = bool(disp.get("show_pending", True))
    show_diag = bool(disp.get("show_diagnostics", True))
    ranked = sorted(teams, key=lambda t: score.get(t["id"], 0), reverse=True)

    lines: list[str] = [
        f"# {settings.get('title', 'BBF Scoreboard')}",
        "",
        f'_Last updated: <span class="local-time" data-utc="{iso}">{utc_fallback}</span>_',
        "",
        "Ranked by overall **Score** (accumulated). Counts are **confirmed** breaks only — an "
        "issue scores once a `/repro-confirmed` comment adds the `valid` label. Column "
        "definitions are below the table.",
        "",
    ]

    headers = ["Rank", "Team", "Score", "Build", "Breaks landed", "Breaks received"]
    aligns = ["---:", "---", "---:", "---", "---:", "---:"]
    if show_pending:
        headers.append("Pending")
        aligns.append("---:")
    headers += ["High-sev received", "Fixed"]
    aligns += ["---:", "---:"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(aligns) + "|")
    for i, t in enumerate(ranked, start=1):
        tid = t["id"]
        cells = [
            str(i), t["name"], f"**{score.get(tid, 0)}**", str(build_status.get(tid, "?")),
            str(breaks_landed.get(tid, 0)), str(breaks_received.get(tid, 0)),
        ]
        if show_pending:
            cells.append(str(pending_received.get(tid, 0)))
        cells += [str(high_sev_received.get(tid, 0)), str(fixed.get(tid, 0))]
        lines.append("| " + " | ".join(cells) + " |")

    lines += ["", "## What the columns mean", ""]
    lines.append(
        f"- **Score** — overall accumulated points: **+{w['landed']}** per break landed, "
        f"**+{w['high_sev_landed']}** extra per high-severity break landed, **+{w['fixed']}** per "
        f"fix, **{w['received']}** per break received. (Weights are set in `settings.yaml`.)"
    )
    lines.append("- **Build** — latest `build-check.yml` run (`success` / `failure` / `no-runs`).")
    lines.append("- **Breaks landed** — confirmed breaks this team filed against *other* teams (offense).")
    lines.append("- **Breaks received** — confirmed breaks *other* teams filed against this team's app (defense).")
    if show_pending:
        lines.append("- **Pending** — breaks filed against this team but not yet `/repro-confirmed` (not scored).")
    lines.append("- **High-sev received** — of the breaks received, how many were self-rated high severity.")
    lines.append("- **Fixed** — received breaks closed by a merged PR (issue labeled `fixed`).")

    lines += [
        "",
        "## Acting on a break (issue comments)",
        "",
        "Put the command on the **first line** of a comment on the Break Report issue:",
        "",
        "- `/repro-confirmed` — *(the targeted team)* you reproduced it against your running app. "
        "Applies the `valid` label and it scores. Add a line on what you observed.",
        "- `/repro-failed` — *(the targeted team)* you could **not** reproduce it. Applies `invalid`; "
        "say what you tried so the breaker can clarify.",
        "- `/out-of-scope` — *(facilitators only)* rule a break invalid (unsafe content, off-protocol).",
        "",
        "Fixes: open a PR whose body says `closes #N`; when it merges, the issue is auto-labeled `fixed`.",
    ]

    if show_pending and pending:
        lines += ["", "## Pending (filed, not yet `/repro-confirmed`)", ""]
        for repo, num, author in pending:
            lines.append(f"- {repo}#{num} by `{author}` — needs `/repro-confirmed` to count")
    if show_diag and self_authored:
        lines += ["", "## Self-authored valid breaks (not counted)", ""]
        for repo, num, author in self_authored:
            lines.append(f"- {repo}#{num} by `{author}` — you can't score a break against your own repo")
    if show_diag and unattributed:
        lines += ["", "## Unattributed issues (author not on any team)", ""]
        for repo, num, author in unattributed:
            lines.append(f"- {repo}#{num} authored by `{author}` — add `{author}` to teams.yaml to attribute it")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true",
                        help="Validate teams.yaml and exit; do not rebuild outputs.")
    args = parser.parse_args()

    settings = load_settings()

    teams_path = ROOT / "teams.yaml"
    if not teams_path.exists():
        print("teams.yaml not found.", file=sys.stderr)
        return 1
    config = yaml.safe_load(teams_path.read_text())
    teams = (config or {}).get("teams") or []
    if not teams:
        print("teams.yaml has no teams; nothing to do.")
        return 0

    problems = validate(teams)
    if problems:
        print("teams.yaml validation FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"teams.yaml OK: {len(teams)} teams.")

    # Always (re)write settings.json so index.html stays in sync with settings.yaml.
    write_settings_json(settings)

    if args.validate_only:
        return 0

    w = settings["scoring"]
    login_to_team: dict[str, str] = {
        login: t["id"] for t in teams for login in (t.get("members") or [])
    }

    breaks_landed: dict[str, int] = defaultdict(int)
    breaks_received: dict[str, int] = defaultdict(int)
    high_sev_received: dict[str, int] = defaultdict(int)
    high_sev_landed: dict[str, int] = defaultdict(int)
    fixed: dict[str, int] = defaultdict(int)
    unattributed: list = []
    pending: list = []
    pending_received: dict[str, int] = defaultdict(int)
    self_authored: list = []

    for t in teams:
        try:
            issues = fetch_issues(t["repo"])
        except subprocess.CalledProcessError as e:
            print(f"WARN: could not fetch issues for {t['repo']}: {e}", file=sys.stderr)
            continue
        for issue in issues:
            labels = {lbl["name"] for lbl in (issue.get("labels") or [])}
            author = (issue.get("author") or {}).get("login") or ""
            if "valid" not in labels:
                if "invalid" not in labels:
                    pending.append((t["repo"], issue["number"], author))
                    pending_received[t["id"]] += 1
                continue
            breaker = login_to_team.get(author)
            if breaker is None:
                unattributed.append((t["repo"], issue["number"], author))
                continue
            if breaker == t["id"]:
                self_authored.append((t["repo"], issue["number"], author))
                continue
            breaks_landed[breaker] += 1
            breaks_received[t["id"]] += 1
            severity = parse_form_field(issue.get("body"), "severity")
            if severity == "high":
                high_sev_received[t["id"]] += 1
                high_sev_landed[breaker] += 1
            if "fixed" in labels:
                fixed[t["id"]] += 1

    score: dict[str, int] = {}
    for t in teams:
        tid = t["id"]
        score[tid] = (
            w["landed"] * breaks_landed.get(tid, 0)
            + w["high_sev_landed"] * high_sev_landed.get(tid, 0)
            + w["fixed"] * fixed.get(tid, 0)
            + w["received"] * breaks_received.get(tid, 0)
        )

    build_status = {t["id"]: fetch_build_status(t["repo"]) for t in teams}

    now = datetime.now(timezone.utc)
    iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    utc_fallback = now.strftime("%Y-%m-%d %H:%M UTC")

    append_history(teams, score, iso, int(settings["history"].get("max_points", 1000)))

    output = render_scoreboard(
        settings=settings, teams=teams, score=score,
        breaks_landed=breaks_landed, breaks_received=breaks_received,
        high_sev_received=high_sev_received, fixed=fixed,
        pending_received=pending_received, build_status=build_status,
        unattributed=unattributed, pending=pending, self_authored=self_authored,
        iso=iso, utc_fallback=utc_fallback,
    )
    (ROOT / "SCOREBOARD.md").write_text(output)
    print(f"SCOREBOARD.md + scores.json + settings.json updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
