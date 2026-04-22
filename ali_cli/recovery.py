"""Error fingerprinting and auto-recovery for Ali CLI.

Maps known error patterns to recovery actions, seeded from
``docs/KNOWN-ISSUES.md`` so the CLI can self-heal without human
intervention.

Recovery stats are tracked in ``ALI_CLI_HOME/recovery.jsonl`` to measure
which patterns recur and how effective the auto-fixes are.
"""

import json
import re
from datetime import datetime, timezone

from ali_cli.config import CONFIG_DIR


RECOVERY_LOG = CONFIG_DIR / "recovery.jsonl"

KNOWN_PATTERNS: list[dict] = [
    {
        "id": "session_expired",
        "match_patterns": [
            "session expired", "not_authenticated", r"hasLogin.*false",
            "Session expired", "SessionExpiredError",
        ],
        "severity": "critical",
        "auto_recoverable": True,
        "recovery_action": "relogin",
        "description": "Alibaba session expired",
        "hint": "Run 'ali login' to refresh. Cookies last ~24h.",
    },
    {
        "id": "baxia_captcha",
        "match_patterns": ["baxia", "captcha", "punish"],
        "severity": "critical",
        "auto_recoverable": False,
        "recovery_action": "switch_to_cloud_browser",
        "description": "Captcha triggered — need cloud browser for this operation",
        "hint": "RFQ posting requires cloud browser. Other ops use local headless.",
    },
    {
        "id": "rfq_ai_stuck",
        "match_patterns": ["Please enter your detailed requirements"],
        "severity": "medium",
        "auto_recoverable": True,
        "recovery_action": "click_apply_modify",
        "description": "AI-generated text not transferred to textarea",
        "hint": "Click 'Apply or modify' button to transfer AI text before submit.",
    },
    {
        "id": "download_cors",
        "match_patterns": ["CORS", "clouddisk", r"fetch.*blocked"],
        "severity": "medium",
        "auto_recoverable": True,
        "recovery_action": "use_new_page",
        "description": "CORS blocking file download",
        "hint": "Use context.new_page() instead of fetch() for cross-origin downloads.",
    },
    {
        "id": "conversations_empty",
        "match_patterns": [
            r"__conversationListFullData__.*undefined",
            r"conversations.*empty", "No conversations",
        ],
        "severity": "low",
        "auto_recoverable": True,
        "recovery_action": "fallback_unread_api",
        "description": "DOM conversation list empty in headless",
        "hint": "Falls back to unread API automatically (v0.3.0+).",
    },
    {
        "id": "context_destroyed",
        "match_patterns": [
            "Execution context was destroyed", "context destroyed",
        ],
        "severity": "medium",
        "auto_recoverable": True,
        "recovery_action": "use_cookies_instead",
        "description": "Navigation during storage_state() call",
        "hint": "Use context.cookies() instead of storage_state().",
    },
    {
        "id": "cloud_browser_token_missing",
        "match_patterns": [r"_tb_token_.*missing", "tb_token"],
        "severity": "medium",
        "auto_recoverable": True,
        "recovery_action": "reload_cookies",
        "description": "Cloud browser lost session token",
        "hint": "Close all pages, reload cookies, re-navigate.",
    },
    {
        "id": "timeout_navigation",
        "match_patterns": [
            "Timeout.*navigat", "Timeout checking login",
            "Timeout.*messenger", "Timeout.*buying leads",
        ],
        "severity": "medium",
        "auto_recoverable": True,
        "recovery_action": "retry_navigation",
        "description": "Navigation timeout — Alibaba slow or network issue",
        "hint": "Retry navigation once. If persistent, check network.",
    },
    {
        "id": "popup_wrong_target",
        "match_patterns": [
            "linkedin", "oauth", "wrong.*popup",
        ],
        "severity": "medium",
        "auto_recoverable": False,
        "recovery_action": "fix_selector",
        "description": "Popup opened wrong page (LinkedIn OAuth instead of RFQ form)",
        "hint": "Use get_by_text('Write RFQ details', exact=True) not class selector.",
    },
    {
        "id": "file_input_silent_fail",
        "match_patterns": [
            r"set_input_files.*nothing", "upload.*failed",
            "file item not found",
        ],
        "severity": "medium",
        "auto_recoverable": False,
        "recovery_action": "check_auth_for_upload",
        "description": "File upload silently failed — likely unauthenticated",
        "hint": "set_input_files only works when authenticated. Check login state.",
    },
]


def fingerprint_error(error_str: str) -> dict | None:
    """Match an error string against known patterns.

    Returns the matching pattern dict, or None if no match.
    """
    error_lower = error_str.lower()
    for pattern in KNOWN_PATTERNS:
        for match_pat in pattern["match_patterns"]:
            try:
                if re.search(match_pat, error_str, re.IGNORECASE):
                    return pattern
            except re.error:
                # Fallback to simple substring match
                if match_pat.lower() in error_lower:
                    return pattern
    return None


def attempt_recovery(pattern_id: str, context: dict) -> bool:
    """Try the known fix for a recognized error pattern.

    Args:
        pattern_id: The pattern's "id" field.
        context: Dict with optional keys: page, browser_manager, command.

    Returns True if recovery succeeded, False otherwise.
    """
    bm = context.get("browser_manager")
    page = context.get("page")

    try:
        if pattern_id == "relogin":
            # Reload cookies from state.json
            if bm:
                from ali_cli.config import load_session
                session = load_session()
                if session:
                    bm._context = bm._browser.new_context(
                        storage_state={k: v for k, v in session.items() if not k.startswith("_")},
                        viewport={"width": 1440, "height": 1080},
                    )
                    bm._page = bm._context.new_page()
                    log_recovery_attempt(pattern_id, True, "Reloaded cookies from state.json")
                    return True
            log_recovery_attempt(pattern_id, False, "No browser manager available")
            return False

        elif pattern_id == "fallback_unread_api":
            # Already handled in code — just log it
            log_recovery_attempt(pattern_id, True, "Falling back to unread API")
            return True

        elif pattern_id == "use_cookies_instead":
            # Use context.cookies() instead of storage_state()
            if bm and bm._context:
                from ali_cli.config import save_cookies
                cookies = bm._context.cookies()
                save_cookies(cookies)
                log_recovery_attempt(pattern_id, True, "Used context.cookies() instead")
                return True
            log_recovery_attempt(pattern_id, False, "No browser context available")
            return False

        elif pattern_id == "reload_cookies":
            # Close pages, reload cookies, re-navigate
            if bm and bm._context:
                from ali_cli.config import load_session
                session = load_session()
                if session and session.get("cookies"):
                    bm._context.add_cookies(session["cookies"])
                    log_recovery_attempt(pattern_id, True, "Reloaded cookies into context")
                    return True
            log_recovery_attempt(pattern_id, False, "No session data")
            return False

        elif pattern_id == "retry_navigation":
            # Simple retry — caller handles this
            log_recovery_attempt(pattern_id, True, "Caller should retry navigation")
            return True

        elif pattern_id == "click_apply_modify":
            # Click "Apply or modify" on the RFQ form
            if page:
                try:
                    page.get_by_text("Apply or modify").click(timeout=5000)
                    page.wait_for_timeout(2000)
                    log_recovery_attempt(pattern_id, True, "Clicked 'Apply or modify'")
                    return True
                except Exception as e:
                    log_recovery_attempt(pattern_id, False, str(e))
                    return False
            return False

        elif pattern_id == "use_new_page":
            # Already fixed in code
            log_recovery_attempt(pattern_id, True, "Using new_page approach")
            return True

        else:
            log_recovery_attempt(pattern_id, False, f"No handler for {pattern_id}")
            return False

    except Exception as e:
        log_recovery_attempt(pattern_id, False, f"Recovery failed: {e}")
        return False


def log_recovery_attempt(pattern_id: str, success: bool, details: str = ""):
    """Track recovery attempt for stats."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "pattern_id": pattern_id,
        "success": success,
        "details": details,
    }
    with open(RECOVERY_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def get_recovery_stats() -> dict:
    """Get recovery attempt statistics.

    Returns:
        {
            "total_attempts": int,
            "by_pattern": {
                "session_expired": {"attempts": 5, "successes": 3, "rate": 0.6},
                ...
            }
        }
    """
    if not RECOVERY_LOG.exists():
        return {"total_attempts": 0, "by_pattern": {}}

    lines = RECOVERY_LOG.read_text().strip().split("\n")
    by_pattern: dict[str, dict] = {}
    total = 0

    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue

        total += 1
        pid = entry.get("pattern_id", "unknown")
        if pid not in by_pattern:
            by_pattern[pid] = {"attempts": 0, "successes": 0}
        by_pattern[pid]["attempts"] += 1
        if entry.get("success"):
            by_pattern[pid]["successes"] += 1

    # Calculate rates
    for pid in by_pattern:
        p = by_pattern[pid]
        p["rate"] = round(p["successes"] / p["attempts"], 2) if p["attempts"] else 0

    return {"total_attempts": total, "by_pattern": by_pattern}
