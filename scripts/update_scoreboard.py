#!/usr/bin/env python3
"""Update the BBF scoreboard data from teams.yaml + settings.yaml via the `gh` CLI.

Writes four files the live page consumes:
  - SCOREBOARD.md : the leaderboard table (Scores), Markdown.
  - scores.json   : per-team score time-series for the graph.
  - breaks.json   : every non-invalid break for the interactive feed.
  - settings.json : the client subset of settings.yaml (theme, toggles, weights,
                    and the data-update cadence read from the cron).

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
BREAKS_JSON = ROOT / "breaks.json"
TEAMS_JSON = ROOT / "teams.json"
SETTINGS_YAML = ROOT / "settings.yaml"
SETTINGS_JSON = ROOT / "settings.json"
UPDATE_WORKFLOW = ROOT / ".github" / "workflows" / "update-scoreboard.yml"

DEFAULT_SETTINGS = {
    "title": "BBF Scoreboard",
    "schedule": {"enabled": True, "update_minutes": 5},
    "display": {
        "live_updates": True,
        "refresh_seconds": 30,
        "theme": "dark",
    },
    "scoring": {"landed": 10, "high_sev_landed": 5, "fixed": 5, "received": -5,
                "duplicate_halflife_minutes": 5},
    "history": {"max_points": 1000},
}


def load_settings() -> dict:
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


def cron_update_minutes(default: int = 5) -> int:
    """Read the actual cron cadence (`*/N * * * *`) from update-scoreboard.yml."""
    try:
        txt = UPDATE_WORKFLOW.read_text()
    except OSError:
        return default
    m = re.search(r"cron:\s*['\"]?\*/(\d+)\s+\*\s+\*\s+\*\s+\*", txt)
    return int(m.group(1)) if m else default


def write_settings_json(settings: dict) -> None:
    d = settings["display"]
    sc = settings["scoring"]
    SETTINGS_JSON.write_text(json.dumps({
        "title": settings.get("title", "BBF Scoreboard"),
        "liveUpdates": bool(d.get("live_updates", True)),
        "refreshSeconds": int(d.get("refresh_seconds", 30)),
        "dataUpdateMinutes": cron_update_minutes(),
        "theme": "light" if str(d.get("theme")) == "light" else "dark",
        "scoring": {
            "landed": sc["landed"], "highSevLanded": sc["high_sev_landed"],
            "fixed": sc["fixed"], "received": sc["received"],
            "duplicateHalflifeMinutes": sc.get("duplicate_halflife_minutes", 5),
        },
    }, indent=2) + "\n")


def write_teams_json(teams: list[dict]) -> None:
    TEAMS_JSON.write_text(json.dumps({
        "teams": [{
            "id": t["id"], "name": t["name"], "repo": t.get("repo", ""),
            "members": list(t.get("members") or []),
        } for t in teams],
    }, indent=2) + "\n")


def write_breaks_json(breaks: list[dict], iso: str) -> None:
    BREAKS_JSON.write_text(json.dumps({
        "updated": iso,
        "breaks": [{
            "status": b["status"], "from": b["frm"], "to": b["to"],
            "prop": b.get("prop") or "", "cls": b.get("cls") or "",
            "sev": (b.get("sev") or "").lower(), "discovery": b.get("discovery") or "",
            "points": b.get("points", 0), "title": b.get("title") or "",
            "url": b.get("url") or "", "number": b.get("number"),
        } for b in breaks],
    }, indent=2) + "\n")


def gh_json(args: list[str]) -> object:
    return json.loads(subprocess.check_output(["gh", *args], text=True))


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
                    f"login '{login}' appears in two teams: {seen_logins[login]} and {tid}"
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
        "--json", "number,author,labels,body,title,url,createdAt",
        "--limit", "1000",
    ])


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def dup_group_key(target_id: str, prop: str | None, cls: str | None) -> tuple:
    """Two confirmed breaks are 'the same' if they hit the same target with the
    same SPEC property (P1..P5) and the same attack class. The first one filed
    earns full credit; later ones decay."""
    m = re.search(r"\bP[1-5]\b", (prop or "").upper())
    return (target_id, m.group(0) if m else "?", (cls or "").strip().lower())


def fetch_build_status(repo: str) -> str:
    try:
        runs = gh_json([
            "run", "list", "-R", repo, "--workflow", "build-check.yml",
            "--limit", "1", "--json", "conclusion",
        ])
        if not runs:
            return "no-runs"
        return runs[0].get("conclusion") or "in-progress"
    except subprocess.CalledProcessError:
        return "error"


FORM_FIELD_HEADINGS = {
    "attack_class": "Attack class",
    "severity": "Severity (self-rated)",
    "property_violated": "Property violated",
    "target_artifact": "Target artifact",
    "discovery": "Discovery method",
}


def normalize_discovery(value: str | None) -> str:
    v = (value or "").lower()
    if "white" in v:
        return "white-box"
    if "black" in v:
        return "black-box"
    return ""


def parse_form_field(body: str | None, field_id: str) -> str | None:
    if not body:
        return None
    label = FORM_FIELD_HEADINGS.get(field_id, field_id)
    pattern = re.compile(rf"^###\s+{re.escape(label)}\s*$", re.MULTILINE)
    match = pattern.search(body)
    if not match:
        return None
    for line in body[match.end():].splitlines():
        s = line.strip()
        if s:
            return s
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


def render_scoreboard(*, settings, teams, score, breaks_landed, breaks_received,
                      high_sev_received, fixed, build_status, iso, utc_fallback) -> str:
    ranked = sorted(teams, key=lambda t: score.get(t["id"], 0), reverse=True)
    lines = [
        f"# {settings.get('title', 'BBF Scoreboard')}",
        "",
        f'_Last updated: <span class="local-time" data-utc="{iso}">{utc_fallback}</span>_',
        "",
        "Ranked by overall **Score**. Confirmed breaks only — a break counts once a "
        "`/repro-confirmed` comment adds the `valid` label.",
        "",
        "| Rank | Team | Score | Build | Landed | Received | High-sev | Fixed |",
        "|---:|---|---:|---|---:|---:|---:|---:|",
    ]
    for i, t in enumerate(ranked, start=1):
        tid = t["id"]
        lines.append(
            f"| {i} | {t['name']} | **{score.get(tid, 0)}** | {build_status.get(tid, '?')} | "
            f"{breaks_landed.get(tid, 0)} | {breaks_received.get(tid, 0)} | "
            f"{high_sev_received.get(tid, 0)} | {fixed.get(tid, 0)} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    settings = load_settings()

    teams_path = ROOT / "teams.yaml"
    if not teams_path.exists():
        print("teams.yaml not found.", file=sys.stderr)
        return 1
    teams = (yaml.safe_load(teams_path.read_text()) or {}).get("teams") or []
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

    write_settings_json(settings)
    write_teams_json(teams)
    if args.validate_only:
        return 0

    w = settings["scoring"]
    login_to_team = {login: t["id"] for t in teams for login in (t.get("members") or [])}
    tid_to_name = {t["id"]: t["name"] for t in teams}

    breaks: list[dict] = []
    breaks_landed: dict[str, int] = defaultdict(int)
    breaks_received: dict[str, int] = defaultdict(int)
    high_sev_received: dict[str, int] = defaultdict(int)
    fixed: dict[str, int] = defaultdict(int)
    scored: list[dict] = []  # confirmed, attributed, non-self breaks (for decay scoring)

    for t in teams:
        try:
            issues = fetch_issues(t["repo"])
        except subprocess.CalledProcessError as e:
            print(f"WARN: could not fetch issues for {t['repo']}: {e}", file=sys.stderr)
            continue
        for issue in issues:
            labels = {lbl["name"] for lbl in (issue.get("labels") or [])}
            author = (issue.get("author") or {}).get("login") or ""
            if "invalid" in labels:
                continue
            status = ("fixed" if "fixed" in labels else "repro") if "valid" in labels else "pending"
            sev = (parse_form_field(issue.get("body"), "severity") or "").lower()
            prop = parse_form_field(issue.get("body"), "property_violated")
            cls = parse_form_field(issue.get("body"), "attack_class")
            frm = tid_to_name.get(login_to_team.get(author, ""), f"@{author}" if author else "?")
            rec = {
                "status": status, "frm": frm, "to": t["name"],
                "prop": prop, "cls": cls, "sev": sev,
                "discovery": normalize_discovery(parse_form_field(issue.get("body"), "discovery")),
                "title": issue.get("title"), "url": issue.get("url"),
                "number": issue.get("number"), "points": 0,
            }
            breaks.append(rec)
            if status == "pending":
                continue
            breaker = login_to_team.get(author)
            if breaker is None or breaker == t["id"]:
                continue  # unattributed or self-break: shown in the feed, not scored
            breaks_landed[breaker] += 1
            breaks_received[t["id"]] += 1
            if sev == "high":
                high_sev_received[t["id"]] += 1
            if status == "fixed":
                fixed[t["id"]] += 1
            scored.append({
                "rec": rec, "breaker": breaker, "target": t["id"], "high": sev == "high",
                "group": dup_group_key(t["id"], prop, cls),
                "created": parse_iso(issue.get("createdAt")) or datetime.now(timezone.utc),
            })

    # First-finder credit with exponential decay for duplicates of the same break.
    # The earliest-filed confirmed break in a (target, property, class) group earns
    # full points; later duplicates decay by 0.5 ** (minutes_after_first / halflife).
    halflife = float(settings["scoring"].get("duplicate_halflife_minutes", 5)) or 5.0
    first_at: dict[tuple, datetime] = {}
    for s in scored:
        g, c = s["group"], s["created"]
        if g not in first_at or c < first_at[g]:
            first_at[g] = c

    landed_pts: dict[str, int] = defaultdict(int)
    received_pts: dict[str, int] = defaultdict(int)
    fixed_pts: dict[str, int] = defaultdict(int)
    for s in scored:
        dt_min = max(0.0, (s["created"] - first_at[s["group"]]).total_seconds() / 60.0)
        factor = 0.5 ** (dt_min / halflife)
        base = w["landed"] + (w["high_sev_landed"] if s["high"] else 0)
        pts = round(base * factor)
        s["rec"]["points"] = pts                       # breaker's credit for this break
        landed_pts[s["breaker"]] += pts
        received_pts[s["target"]] += round(w["received"] * factor)
        if s["rec"]["status"] == "fixed":
            fixed_pts[s["target"]] += w["fixed"]       # fixing always pays full

    score = {
        t["id"]: landed_pts.get(t["id"], 0) + fixed_pts.get(t["id"], 0) + received_pts.get(t["id"], 0)
        for t in teams
    }

    build_status = {t["id"]: fetch_build_status(t["repo"]) for t in teams}

    now = datetime.now(timezone.utc)
    iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    utc_fallback = now.strftime("%Y-%m-%d %H:%M UTC")

    append_history(teams, score, iso, int(settings["history"].get("max_points", 1000)))
    write_breaks_json(breaks, iso)
    (ROOT / "SCOREBOARD.md").write_text(render_scoreboard(
        settings=settings, teams=teams, score=score, breaks_landed=breaks_landed,
        breaks_received=breaks_received, high_sev_received=high_sev_received,
        fixed=fixed, build_status=build_status, iso=iso, utc_fallback=utc_fallback,
    ))
    print("SCOREBOARD.md + scores.json + breaks.json + settings.json updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
