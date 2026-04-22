"""Error handling and step logging for Ali CLI.

Every step in the browser automation pipeline is logged here.
Errors are caught, logged with context, and surfaced as user-friendly messages.
Successful steps are also logged so you can trace exactly what happened.

Log files (all in ~/.ali-cli/):
  errors.jsonl       — errors only, for quick diagnosis
  run.jsonl          — per-run step trace (success + failure)
  skill-reports.jsonl — skill execution feedback for cron analysis
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from contextlib import contextmanager

from ali_cli.config import CONFIG_DIR


ERROR_LOG = CONFIG_DIR / "errors.jsonl"
RUN_LOG = CONFIG_DIR / "run.jsonl"
SKILL_REPORTS = CONFIG_DIR / "skill-reports.jsonl"

# Current run context (set at start of each CLI command)
_current_run = {
    "run_id": None,
    "command": None,
    "context": None,
}


class AliError(Exception):
    """User-friendly error from Ali CLI browser automation.

    Attributes:
        step: The automation step that failed.
        hint: Recovery hint for the user.
        command: The CLI command that was running.
    """

    def __init__(self, message: str, step: str = "", hint: str = "", command: str = ""):
        self.step = step
        self.hint = hint
        self.command = command
        super().__init__(message)

    def __str__(self):
        msg = super().__str__()
        if self.hint:
            msg += f"\n  Hint: {self.hint}"
        if self.step:
            msg += f"\n  Failed step: {self.step}"
            msg += f"\n  Debug log: ~/.ali-cli/errors.jsonl"
        return msg


def start_run(command: str, context: dict | None = None) -> str:
    """Initialize a new run context. Call at the start of each CLI command.

    Returns the run_id for reference.
    """
    import uuid
    run_id = str(uuid.uuid4())[:8]
    _current_run["run_id"] = run_id
    _current_run["command"] = command
    _current_run["context"] = context
    log_step(command, "run_start", status="started", details=context)
    return run_id


def log_step(command: str, step: str, status: str = "ok",
             details: dict | None = None, url: str = "", page_snippet: str = ""):
    """Log a step (success or failure) to run.jsonl for tracing."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": _current_run.get("run_id", ""),
        "command": command,
        "step": step,
        "status": status,
        "url": url,
    }
    if details:
        entry["details"] = details
    if page_snippet:
        entry["page_snippet"] = page_snippet[:300]
    with open(RUN_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def log_error(command: str, step: str, error, url: str = "",
              page_snippet: str = "", hint: str = ""):
    """Log an error to errors.jsonl and run.jsonl for debugging.

    Always call this before raising AliError so the error is persisted
    even if the exception is caught upstream.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": _current_run.get("run_id", ""),
        "command": command,
        "step": step,
        "status": "error",
        "error": str(error),
        "url": url,
        "page_snippet": page_snippet[:500] if page_snippet else "",
    }
    if hint:
        entry["hint"] = hint
    with open(ERROR_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    # Also log to run trace
    log_step(command, step, status="error", url=url,
             details={"error": str(error)[:200], "hint": hint})


@contextmanager
def step(command: str, step_name: str, page=None,
         required: bool = True, on_error: str = "raise"):
    """Context manager for logging a browser automation step.

    Usage:
        with step("monitor", "get_unread_summary", page=page) as s:
            data = bm.get_unread_summary()
            s.log("got unread summary", {"count": len(data)})

    Args:
        command: CLI command name (e.g. "monitor", "status").
        step_name: Descriptive step name (e.g. "get_unread_summary").
        page: Playwright page (for URL/content capture on error).
        required: If True, raise AliError on failure. If False, log and continue.
        on_error: "raise" (default) or "continue".
    """
    class StepContext:
        def log(self, msg: str, details: dict | None = None):
            log_step(command, step_name, status="ok",
                     details={"msg": msg, **(details or {})},
                     url=_safe_url(page))

    ctx = StepContext()
    try:
        yield ctx
        log_step(command, step_name, status="ok", url=_safe_url(page))
    except AliError:
        raise  # Already logged
    except Exception as e:
        url = _safe_url(page)
        snippet = _safe_snippet(page)
        log_error(command, step_name, e, url=url, page_snippet=snippet)
        if required and on_error == "raise":
            raise AliError(
                f"Step '{step_name}' failed: {e}",
                step=step_name,
                hint="Check ~/.ali-cli/errors.jsonl for details.",
                command=command,
            ) from e
        # on_error == "continue" or required=False — log and move on


def _safe_url(page) -> str:
    """Get page URL without raising."""
    try:
        return page.url if page else ""
    except Exception:
        return ""


def _safe_snippet(page) -> str:
    """Get page text snippet without raising."""
    try:
        if page:
            return (page.text_content("body") or "")[:300]
    except Exception:
        pass
    return ""


def get_recent_errors(limit: int = 10, since_hours: float | None = None,
                      command_filter: str | None = None) -> list[dict]:
    """Return the most recent error entries from errors.jsonl.

    Args:
        limit: Max number of errors to return.
        since_hours: Only return errors from the last N hours.
        command_filter: Only return errors from this command.
    """
    if not ERROR_LOG.exists():
        return []

    cutoff = None
    if since_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    lines = ERROR_LOG.read_text().strip().split("\n")
    entries = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue

        if command_filter and entry.get("command") != command_filter:
            continue
        if cutoff:
            try:
                ts = datetime.fromisoformat(entry["ts"])
                if ts < cutoff:
                    continue
            except (KeyError, ValueError):
                continue

        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def get_run_trace(run_id: str | None = None) -> list[dict]:
    """Return all steps for a run (latest run if run_id is None)."""
    if not RUN_LOG.exists():
        return []

    lines = RUN_LOG.read_text().strip().split("\n")

    if run_id is None:
        # Find the latest run_id
        run_id = _current_run.get("run_id")
        if not run_id:
            # Scan backwards for most recent run_start
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("step") == "run_start":
                        run_id = entry.get("run_id")
                        break
                except Exception:
                    continue

    entries = []
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if not run_id or entry.get("run_id") == run_id:
                entries.append(entry)
        except Exception:
            pass
    return entries


def get_error_summary(days: int = 7) -> dict:
    """Aggregate errors by command/step for doctor analysis.

    Returns:
        {
            "total_errors": int,
            "by_command": {"monitor": 5, "status": 2, ...},
            "by_step": {"get_unread_summary": 3, ...},
            "by_error": {"Session expired...": 4, ...},
            "period_days": int,
        }
    """
    errors = get_recent_errors(limit=10000, since_hours=days * 24)

    by_command: dict[str, int] = {}
    by_step: dict[str, int] = {}
    by_error: dict[str, int] = {}

    for e in errors:
        cmd = e.get("command", "unknown")
        stp = e.get("step", "unknown")
        err = str(e.get("error", ""))[:80]

        by_command[cmd] = by_command.get(cmd, 0) + 1
        by_step[stp] = by_step.get(stp, 0) + 1
        if err:
            by_error[err] = by_error.get(err, 0) + 1

    return {
        "total_errors": len(errors),
        "by_command": dict(sorted(by_command.items(), key=lambda x: -x[1])),
        "by_step": dict(sorted(by_step.items(), key=lambda x: -x[1])),
        "by_error": dict(sorted(by_error.items(), key=lambda x: -x[1])),
        "period_days": days,
    }


def log_skill_report(skill: str, status: str, steps_ok: int = 0,
                     steps_failed: int = 0, duration: float = 0,
                     issues: list | None = None,
                     improvements: list | None = None):
    """Write a skill execution report to skill-reports.jsonl."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "skill": skill,
        "status": status,
        "steps_ok": steps_ok,
        "steps_failed": steps_failed,
        "duration": duration,
        "issues": issues or [],
        "improvements": improvements or [],
    }
    with open(SKILL_REPORTS, "a") as f:
        f.write(json.dumps(entry) + "\n")


def get_skill_reports(limit: int = 50, skill_filter: str | None = None) -> list[dict]:
    """Read recent skill reports."""
    if not SKILL_REPORTS.exists():
        return []
    lines = SKILL_REPORTS.read_text().strip().split("\n")
    entries = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if skill_filter and entry.get("skill") != skill_filter:
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries
