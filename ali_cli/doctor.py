"""Ali CLI Doctor — self-test, self-healing, and error analysis.

Run `ali doctor` to check all critical systems.
Run `ali doctor --fix` to auto-repair failures using a coding agent.
Run `ali doctor --analyze` to review error patterns and recovery stats.
Run `ali doctor --heal` to attempt auto-recovery for known patterns.
Run `ali doctor --log-issue "description" --error "msg"` to log a new issue.

Used by crons to maintain CLI health over time.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from ali_cli.config import get_home

# User-local issue log (ALI_CLI_HOME/issues.md). Kept out of the installed
# package so the CLI doesn't try to write into its own install directory.
CLI_DIR = Path(__file__).resolve().parent.parent
ISSUES_FILE = get_home() / "issues.md"


# ── Issue logging ─────────────────────────────────────────────────────

def log_issue(description: str, error: str, root_cause: str = "", fix_desc: str = "",
              command: str = "", status: str = "OPEN", commit: str = "") -> str:
    """Append a new issue to ALI_CLI_HOME/issues.md. Returns the issue ID.

    Creates the file on first call with a header so subsequent runs can
    continue numbering entries.
    """
    ISSUES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ISSUES_FILE.exists():
        ISSUES_FILE.write_text(
            "# ali-cli local issue log\n\n"
            "*Issues captured on this machine by `ali doctor --log-issue`.*\n\n"
        )

    content = ISSUES_FILE.read_text()

    # Find next issue number
    import re
    existing = re.findall(r"### ISSUE-(\d+):", content)
    next_num = max((int(n) for n in existing), default=0) + 1
    issue_id = f"ISSUE-{next_num:03d}"

    date_str = datetime.now().strftime("%Y-%m-%d")
    entry = f"""
### {issue_id}: {description}
- **Date:** {date_str}
- **Command:** {command or "unknown"}
- **Error:** {error}
- **Root Cause:** {root_cause or "TBD"}
- **Fix:** {fix_desc or "TBD"}
- **Commit:** {commit or "pending"}
- **Status:** {status}
"""
    # Append before end of file
    ISSUES_FILE.write_text(content.rstrip() + "\n" + entry + "\n")
    return issue_id


def update_issue_status(issue_id: str, status: str, fix_desc: str = "", commit: str = ""):
    """Update an existing issue's status/fix in the local issues log."""
    if not ISSUES_FILE.exists():
        return
    content = ISSUES_FILE.read_text()
    if issue_id not in content:
        return

    lines = content.split("\n")
    in_issue = False
    for i, line in enumerate(lines):
        if f"### {issue_id}:" in line:
            in_issue = True
        if in_issue:
            if fix_desc and line.startswith("- **Fix:**"):
                lines[i] = f"- **Fix:** {fix_desc}"
            if commit and line.startswith("- **Commit:**"):
                lines[i] = f"- **Commit:** {commit}"
            if line.startswith("- **Status:**"):
                lines[i] = f"- **Status:** {status}"
                break

    ISSUES_FILE.write_text("\n".join(lines))


# ── Self-test suite ───────────────────────────────────────────────────

def run_cmd(args: list, timeout: int = 60) -> tuple[int, str, str]:
    """Run an ali CLI command, return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        ["ali"] + args,
        capture_output=True, text=True, timeout=timeout,
        cwd=str(CLI_DIR),
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def check_health_quick() -> dict:
    """Fast check: are cookies fresh?"""
    code, out, err = run_cmd(["health", "--quick", "--json"], timeout=10)
    if code != 0:
        return {"ok": False, "test": "health_quick", "error": err or out}
    try:
        data = json.loads(out)
        age = data.get("cookie_age_hours", 99)
        ok = age < 20
        return {"ok": ok, "test": "health_quick", "cookie_age_hours": age,
                "error": None if ok else f"Cookies {age:.1f}h old (>20h threshold)"}
    except Exception as e:
        return {"ok": False, "test": "health_quick", "error": str(e)}


def check_session_live() -> dict:
    """Live check: can we hit the Alibaba API?"""
    code, out, err = run_cmd(["status", "--json"], timeout=45)
    if code == 2:
        return {"ok": False, "test": "session_live", "error": "Session expired (exit 2)"}
    if code != 0:
        return {"ok": False, "test": "session_live", "error": err or out}
    try:
        data = json.loads(out)
        ok = data.get("logged_in", False)
        return {"ok": ok, "test": "session_live",
                "unread": data.get("unread_count", 0),
                "error": None if ok else "Not logged in"}
    except Exception as e:
        return {"ok": False, "test": "session_live", "error": str(e)}


def check_messages() -> dict:
    """Can we list messages?"""
    code, out, err = run_cmd(["messages", "--unread", "--json"], timeout=45)
    if code != 0:
        return {"ok": False, "test": "messages", "error": err or out}
    try:
        data = json.loads(out)
        ok = isinstance(data, list)
        return {"ok": ok, "test": "messages", "count": len(data),
                "error": None if ok else "Expected list"}
    except Exception as e:
        return {"ok": False, "test": "messages", "error": str(e)}


def check_rfqs() -> dict:
    """Can we list RFQs?"""
    code, out, err = run_cmd(["rfqs", "--json", "--limit", "5"], timeout=45)
    if code != 0:
        return {"ok": False, "test": "rfqs", "error": err or out}
    try:
        data = json.loads(out)
        ok = "total" in data and "rfqs" in data
        return {"ok": ok, "test": "rfqs", "total": data.get("total", 0),
                "error": None if ok else "Unexpected shape"}
    except Exception as e:
        return {"ok": False, "test": "rfqs", "error": str(e)}


def check_post_rfq_dry() -> dict:
    """Can we open the RFQ posting form? (dry-run, no actual post)

    Looks for a test xlsx in ALI_CLI_HOME/test-fixtures/*.xlsx, or set
    ALI_CLI_TEST_XLSX to a specific file path.
    """
    import glob
    from ali_cli.config import get_home

    test_path = os.environ.get("ALI_CLI_TEST_XLSX", "")
    if test_path and os.path.exists(test_path):
        xlsx_files = [test_path]
    else:
        xlsx_files = glob.glob(str(get_home() / "test-fixtures" / "*.xlsx"))
    if not xlsx_files:
        return {"ok": None, "test": "post_rfq",
                "error": "No xlsx files found for test (set ALI_CLI_TEST_XLSX or add to ALI_CLI_HOME/test-fixtures/)"}

    code, out, err = run_cmd([
        "post-rfq",
        "--subject", "Test RFQ for doctor health check",
        "--quantity", "100",
        "--attach", xlsx_files[0],
        "--dry-run", "--json",
    ], timeout=180)

    if code != 0:
        error_msg = ""
        try:
            data = json.loads(out)
            error_msg = data.get("error", out)
        except Exception:
            error_msg = out or err
        return {"ok": False, "test": "post_rfq", "error": error_msg}

    try:
        data = json.loads(out)
        ok = data.get("success") and data.get("dry_run")
        fd = data.get("form_data", {})
        return {
            "ok": ok, "test": "post_rfq",
            "product": fd.get("product_name", ""),
            "category": fd.get("category", ""),
            "detail_len": len(fd.get("detail", "")),
            "error": None if ok else data.get("error", "unknown"),
        }
    except Exception as e:
        return {"ok": False, "test": "post_rfq", "error": str(e)}


# ── Error analysis ────────────────────────────────────────────────────

def run_analyze() -> dict:
    """Analyze error patterns and recovery stats from jsonl logs."""
    from ali_cli.errors import get_error_summary, get_skill_reports
    from ali_cli.recovery import get_recovery_stats, KNOWN_PATTERNS

    error_summary = get_error_summary(days=7)
    recovery_stats = get_recovery_stats()
    skill_reports = get_skill_reports(limit=100)

    # Skill health
    skill_health: dict[str, dict] = {}
    for r in skill_reports:
        s = r.get("skill", "unknown")
        if s not in skill_health:
            skill_health[s] = {"runs": 0, "success": 0, "failure": 0, "partial": 0}
        skill_health[s]["runs"] += 1
        status = r.get("status", "")
        if status in skill_health[s]:
            skill_health[s][status] += 1

    # Recurring patterns (3+ occurrences)
    recurring = {k: v for k, v in error_summary.get("by_error", {}).items() if v >= 3}

    return {
        "error_summary": error_summary,
        "recovery_stats": recovery_stats,
        "skill_health": skill_health,
        "recurring_patterns": recurring,
        "known_patterns_count": len(KNOWN_PATTERNS),
    }


def run_heal() -> dict:
    """Attempt auto-recovery for all known auto-recoverable patterns."""
    from ali_cli.recovery import KNOWN_PATTERNS, attempt_recovery, get_recovery_stats

    results = []
    for pattern in KNOWN_PATTERNS:
        if not pattern.get("auto_recoverable"):
            continue
        # Only attempt recovery for patterns that don't need a browser context
        if pattern["recovery_action"] in ("relogin", "retry_navigation", "click_apply_modify"):
            continue  # These need active browser context

        success = attempt_recovery(pattern["recovery_action"], {})
        results.append({
            "pattern": pattern["id"],
            "action": pattern["recovery_action"],
            "success": success,
        })

    stats = get_recovery_stats()
    return {
        "attempted": len(results),
        "succeeded": sum(1 for r in results if r["success"]),
        "results": results,
        "overall_stats": stats,
    }


# ── Doctor runner ─────────────────────────────────────────────────────

TESTS = [
    ("Cookie freshness",     check_health_quick,   "fast"),
    ("Live API session",     check_session_live,   "live"),
    ("Messages listing",     check_messages,       "live"),
    ("RFQ listing",          check_rfqs,           "live"),
    ("RFQ posting (dry)",    check_post_rfq_dry,   "slow"),
]


def run_doctor(fast_only: bool = False, skip_post: bool = False,
               auto_fix: bool = False, verbose: bool = False) -> dict:
    """Run the doctor test suite. Returns summary dict."""
    results = []
    failed = []
    start = time.time()

    print("=" * 60)
    print("Ali CLI Doctor")
    print("=" * 60)

    for name, fn, speed in TESTS:
        if fast_only and speed != "fast":
            continue
        if skip_post and speed == "slow":
            continue

        print(f"\n  Checking: {name}...")
        t0 = time.time()
        try:
            result = fn()
        except subprocess.TimeoutExpired:
            result = {"ok": False, "test": name, "error": "timeout"}
        except Exception as e:
            result = {"ok": False, "test": name, "error": str(e)}

        elapsed = time.time() - t0
        result["name"] = name
        result["elapsed"] = round(elapsed, 1)
        results.append(result)

        ok = result.get("ok")
        if ok is None:
            print(f"  ⚪ SKIP — {result.get('error', '')}")
        elif ok:
            extras = []
            if "unread" in result:
                extras.append(f"unread={result['unread']}")
            if "count" in result:
                extras.append(f"count={result['count']}")
            if "total" in result:
                extras.append(f"total={result['total']}")
            if "product" in result and result["product"]:
                extras.append(f"product='{result['product'][:40]}'")
            if "category" in result:
                extras.append(f"category='{result['category']}'")
            if "detail_len" in result:
                extras.append(f"detail={result['detail_len']}chars")
            print(f"  ✅ PASS ({elapsed:.1f}s){' — ' + ', '.join(extras) if extras else ''}")
        else:
            print(f"  ❌ FAIL ({elapsed:.1f}s) — {result.get('error', 'unknown')}")
            failed.append(result)

    total_elapsed = time.time() - start
    passed = sum(1 for r in results if r.get("ok") is True)
    skipped = sum(1 for r in results if r.get("ok") is None)
    total = len(results)

    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed, {len(failed)} failed, {skipped} skipped")
    print(f"Time: {total_elapsed:.1f}s")

    if failed:
        print(f"\n❌ FAILED TESTS:")
        for f in failed:
            print(f"  - {f['name']}: {f.get('error', '?')}")

        if auto_fix:
            print(f"\n🔧 Auto-fix mode enabled — spawning repair agent...")
            _spawn_fix_agent(failed)
        else:
            print("\n  Run `ali doctor --fix` to write a repair report, "
                  "or check docs/KNOWN-ISSUES.md")

    return {
        "passed": passed,
        "failed": len(failed),
        "skipped": skipped,
        "total": total,
        "failures": failed,
        "elapsed": round(total_elapsed, 1),
        "healthy": len(failed) == 0,
    }


def _spawn_fix_agent(failures: list):
    """Write a repair-needed report for manual pickup.

    In the original private deployment this spawned a coding agent to self-heal;
    in the public build we just dump a report so the user (or their own
    coding-agent harness) can decide what to do.
    """
    failure_summary = "\n".join(
        f"- {f['name']}: {f.get('error', '?')}" for f in failures
    )
    issues_content = ISSUES_FILE.read_text()[:3000] if ISSUES_FILE.exists() else ""

    repair_file = CLI_DIR / "REPAIR-NEEDED.md"
    repair_file.write_text(
        f"# Auto-Repair Needed\n\n{datetime.now().isoformat()}\n\n"
        f"## Failures\n{failure_summary}\n\n"
        f"## Known Issues\n{issues_content}\n"
    )
    print(f"  Repair task written to {repair_file}")
