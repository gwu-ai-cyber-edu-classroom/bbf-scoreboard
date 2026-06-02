#!/usr/bin/env python3
"""Update SCOREBOARD.md from teams.yaml using the `gh` CLI.

Logic:
  1. Load teams.yaml. Validate schema. Resolve every repo and every member
     login. Exit non-zero on failure -- the workflow run will show red.
  2. For each team repo, list all issues with author + labels (one gh call).
  3. For each `valid`-labeled issue:
       - look up the author's team via the login -> team_id map
       - increment breaks_landed for the author's team
       - increment breaks_received for the repo's team
       - increment severity / fixed counters as labels indicate
  4. For each team repo, fetch the latest `build-check.yml` run conclusion.
  5. Render SCOREBOARD.md.

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


def gh_json(args: list[str]) -> object:
    """Run `gh <args>` and parse its JSON output."""
    out = subprocess.check_output(["gh", *args], text=True)
    return json.loads(out)


def gh_check(args: list[str]) -> bool:
    """Run `gh <args>`; return True if exit 0, False otherwise."""
    return (
        subprocess.run(["gh", *args], capture_output=True).returncode == 0
    )


def validate(teams: list[dict]) -> list[str]:
    """Schema-validate teams.yaml. Returns list of problem strings; empty == OK."""
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
        # Resolve repo
        if repo and not gh_check(["repo", "view", repo]):
            problems.append(f"repo not found on GitHub: {repo}")
        # Resolve each login
        for login in members:
            if not gh_check(["api", f"users/{login}"]):
                problems.append(f"login not found on GitHub: {login}")
    return problems


def fetch_issues(repo: str) -> list[dict]:
    """Return all issues in repo with number, author, labels, body."""
    return gh_json([
        "issue", "list", "-R", repo, "--state", "all",
        "--json", "number,author,labels,body",
        "--limit", "1000",
    ])


def fetch_build_status(repo: str) -> str:
    """Conclusion of the most recent run of build-check.yml on the repo."""
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


# Map our YAML form `id` to the heading label GitHub renders in the issue body.
FORM_FIELD_HEADINGS = {
    "attack_class": "Attack class",
    "severity": "Severity (self-rated)",
}


def parse_form_field(body: str | None, field_id: str) -> str | None:
    """Extract a field value from a GitHub issue-form body.

    Issue forms render as Markdown sections separated by `### <Label>` headings,
    followed by a blank line and the value. This pulls the first non-empty line
    after the heading whose text matches FORM_FIELD_HEADINGS[field_id].
    """
    if not body:
        return None
    label = FORM_FIELD_HEADINGS.get(field_id, field_id)
    pattern = re.compile(rf"^###\s+{re.escape(label)}\s*$", re.MULTILINE)
    match = pattern.search(body)
    if not match:
        return None
    rest = body[match.end():].splitlines()
    for line in rest:
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def render_scoreboard(
    teams: list[dict],
    breaks_landed: dict,
    breaks_received: dict,
    high_sev_received: dict,
    fixed: dict,
    pending_received: dict,
    build_status: dict,
    unattributed: list,
    pending: list,
    self_authored: list,
) -> str:
    now = datetime.now(timezone.utc)
    iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    utc_fallback = now.strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# BBF Day Scoreboard",
        "",
        # index.html rewrites this to the viewer's local timezone; the UTC text
        # is the fallback shown in a raw Markdown view.
        f'_Last updated: <span class="local-time" data-utc="{iso}">{utc_fallback}</span>_',
        "",
        "Counts reflect **confirmed** breaks only — an issue counts once it is labeled "
        "`valid` (a team member commented `/repro-confirmed`). Filed-but-unconfirmed issues "
        "are listed under _Pending_ below, not in the table.",
        "",
        "| Team | Build | Breaks landed | Breaks received | High-sev received | Fixed |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for t in teams:
        tid = t["id"]
        lines.append(
            f"| {t['name']} | {build_status.get(tid, '?')} | "
            f"{breaks_landed.get(tid, 0)} | "
            f"{breaks_received.get(tid, 0)} | "
            f"{high_sev_received.get(tid, 0)} | "
            f"{fixed.get(tid, 0)} |"
        )

    # Diagnostics — so no filed issue ever vanishes without explanation.
    if pending:
        lines += ["", "## Pending (filed, not yet `/repro-confirmed`)", ""]
        for repo, num, author in pending:
            lines.append(f"- {repo}#{num} by `{author}` — needs `/repro-confirmed` to count")
    if self_authored:
        lines += ["", "## Self-authored valid breaks (not counted)", ""]
        for repo, num, author in self_authored:
            lines.append(
                f"- {repo}#{num} by `{author}` — you can't score a break against your own repo"
            )
    if unattributed:
        lines += ["", "## Unattributed issues (author not on any team)", ""]
        for repo, num, author in unattributed:
            lines.append(
                f"- {repo}#{num} authored by `{author}` — add `{author}` to teams.yaml to attribute it"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--validate-only", action="store_true",
        help="Validate teams.yaml and exit; do not rebuild SCOREBOARD.md.",
    )
    args = parser.parse_args()

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

    if args.validate_only:
        return 0

    login_to_team: dict[str, str] = {
        login: t["id"]
        for t in teams
        for login in (t.get("members") or [])
    }

    breaks_landed: dict[str, int] = defaultdict(int)
    breaks_received: dict[str, int] = defaultdict(int)
    high_sev_received: dict[str, int] = defaultdict(int)
    fixed: dict[str, int] = defaultdict(int)
    unattributed: list = []
    pending: list = []
    self_authored: list = []

    for t in teams:
        try:
            issues = fetch_issues(t["repo"])
        except subprocess.CalledProcessError as e:
            print(
                f"WARN: could not fetch issues for {t['repo']}: {e}",
                file=sys.stderr,
            )
            continue
        for issue in issues:
            labels = {lbl["name"] for lbl in (issue.get("labels") or [])}
            author = (issue.get("author") or {}).get("login") or ""
            if "valid" not in labels:
                # Filed but not confirmed. Surface it (unless it was ruled invalid)
                # so a created issue never silently disappears from the board.
                if "invalid" not in labels:
                    pending.append((t["repo"], issue["number"], author))
                continue
            breaker = login_to_team.get(author)
            if breaker is None:
                unattributed.append((t["repo"], issue["number"], author))
                continue
            if breaker == t["id"]:
                # A break against your own repo doesn't score — but show it.
                self_authored.append((t["repo"], issue["number"], author))
                continue
            breaks_landed[breaker] += 1
            breaks_received[t["id"]] += 1
            severity = parse_form_field(issue.get("body"), "severity")
            if severity == "high":
                high_sev_received[t["id"]] += 1
            if "fixed" in labels:
                fixed[t["id"]] += 1

    build_status = {t["id"]: fetch_build_status(t["repo"]) for t in teams}

    output = render_scoreboard(
        teams=teams,
        breaks_landed=breaks_landed,
        breaks_received=breaks_received,
        high_sev_received=high_sev_received,
        fixed=fixed,
        build_status=build_status,
        unattributed=unattributed,
        pending=pending,
        self_authored=self_authored,
    )
    (ROOT / "SCOREBOARD.md").write_text(output)
    print(f"SCOREBOARD.md updated ({len(output)} bytes).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
