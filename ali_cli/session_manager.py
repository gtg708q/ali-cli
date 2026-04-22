"""Session Manager — LOCAL-FIRST browser architecture for Alibaba CLI.

Architecture:
    ┌─────────────────────────────────────┐
    │  DAILY LOGIN (Browser Use cloud)     │
    │  → Saves cookies to state.json      │
    │  → $0.06/day                        │
    └─────────────────┬───────────────────┘
                      ↓
    ┌─────────────────────────────────────┐
    │  ALL OPERATIONS (local Chromium)     │
    │  → Loads state.json cookies         │
    │  → page.evaluate() for API calls    │
    │  → FREE, no CDP URL, no config      │
    └─────────────────────────────────────┘
                      ↓
    ┌─────────────────────────────────────┐
    │  KEEPALIVE CRON (every 8 hours)     │
    │  → Local Chromium + saved cookies   │
    │  → Navigates to alibaba.com         │
    │  → Refreshes session cookies        │
    └─────────────────────────────────────┘

Usage:
    from ali_cli.session_manager import get_browser, refresh_login

    # Normal operations — uses local browser with saved cookies
    with get_browser() as bm:
        bm.ensure_logged_in()
        data = bm.get_unread_summary()

    # Login refresh — uses Browser Use cloud, saves cookies
    refresh_login()  # Called by daily cron
"""

import json
import os
import time
from pathlib import Path

import requests

from ali_cli.config import (
    get_home,
    get_browser_use_api_key,
    get_browser_use_profile_id,
)

# Session files
ALI_DIR = get_home()
STATE_FILE = ALI_DIR / "state.json"        # Playwright storage_state (cookies + localStorage)
COOKIES_FILE = ALI_DIR / "cookies.json"     # Raw cookie list
CLOUD_SESSION_FILE = ALI_DIR / "browser-session.json"  # Active Browser Use session (if any)
LOGIN_STATUS_FILE = ALI_DIR / "login-status.json"      # Last login timestamp + result

BROWSER_USE_API = "https://api.browser-use.com/api/v2/browsers"


def _get_api_key():
    """Load Browser Use API key from config, env, or ALI_CLI_HOME/.env."""
    return get_browser_use_api_key()


def _ensure_dir():
    ALI_DIR.mkdir(parents=True, exist_ok=True)


# ── State management ─────────────────────────────────────────────────

def save_state(storage_state, cookies=None):
    """Save Playwright storage_state (and optional raw cookies) to disk."""
    _ensure_dir()
    STATE_FILE.write_text(json.dumps(storage_state, indent=2))
    os.chmod(STATE_FILE, 0o600)
    if cookies:
        COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
        os.chmod(COOKIES_FILE, 0o600)


def load_state():
    """Load saved Playwright storage_state. Returns dict or None."""
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text())
        # Strip custom metadata keys before passing to Playwright
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return None


def state_age_hours():
    """Return age of saved state in hours, or None if no state."""
    if not STATE_FILE.exists():
        return None
    mtime = STATE_FILE.stat().st_mtime
    return (time.time() - mtime) / 3600


def update_login_status(success, method="unknown"):
    """Record when we last logged in."""
    _ensure_dir()
    LOGIN_STATUS_FILE.write_text(json.dumps({
        "success": success,
        "method": method,
        "timestamp": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=2))


# ── Local browser (primary path — all normal operations) ─────────────

def get_browser(target_url=None):
    """Get a local headless browser with saved Alibaba cookies.
    
    This is the PRIMARY entry point for all CLI commands.
    Uses local Chromium — no Browser Use, no CDP URL, no config.patch.
    
    Returns a BrowserManager context manager. If cookies are expired,
    the browser will still work — the caller should check is_logged_in()
    and call refresh_login() if needed.
    """
    from ali_cli.browser import BrowserManager
    return BrowserManager(headless=True, timeout=30000)


def check_logged_in(bm, target_url=None):
    """Navigate to target URL and check if we're logged in.
    
    Returns True if logged in, False if redirected to login.
    """
    url = target_url or "https://message.alibaba.com/message/messenger.htm"
    bm.page.goto(url, wait_until="domcontentloaded", timeout=30000)
    bm.page.wait_for_timeout(4000)
    return "login" not in bm.page.url.lower()


# ── Cloud browser (login only) ──────────────────────────────────────

def load_cloud_session():
    """Load saved cloud browser session. Returns dict or None."""
    if not CLOUD_SESSION_FILE.exists():
        return None
    try:
        return json.loads(CLOUD_SESSION_FILE.read_text())
    except Exception:
        return None


def save_cloud_session(session_id, cdp_url, timeout_at=None):
    """Save cloud browser session for reuse."""
    _ensure_dir()
    CLOUD_SESSION_FILE.write_text(json.dumps({
        "session_id": session_id,
        "cdp_url": cdp_url,
        "timeout_at": timeout_at,
        "created_at": time.time(),
        "created_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=2))
    os.chmod(CLOUD_SESSION_FILE, 0o600)


def clear_cloud_session():
    """Remove saved cloud browser session."""
    if CLOUD_SESSION_FILE.exists():
        CLOUD_SESSION_FILE.unlink()


def _check_session_alive(api_key, session_id):
    """Check if a Browser Use session is still active."""
    try:
        resp = requests.get(
            f"{BROWSER_USE_API}/{session_id}",
            headers={"X-Browser-Use-API-Key": api_key},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("status") == "active"
    except Exception:
        pass
    return False


def start_cloud_browser(timeout_min=10):
    """Start a Browser Use cloud browser session for login.
    
    Returns (cdp_url, session_id). Short timeout since we only need it for login.
    """
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("No Browser Use API key configured.")

    # Check for existing session
    saved = load_cloud_session()
    if saved:
        sid = saved.get("session_id")
        cdp = saved.get("cdp_url")
        if sid and cdp and _check_session_alive(api_key, sid):
            return cdp, sid
        clear_cloud_session()

    # Start new
    profile_id = get_browser_use_profile_id()
    if not profile_id:
        raise RuntimeError(
            "No Browser Use profile ID configured. "
            "Set `browser_use_profile_id` in ~/.ali-cli/config.json "
            "or BROWSER_USE_PROFILE_ID env var."
        )
    resp = requests.post(
        BROWSER_USE_API,
        headers={"X-Browser-Use-API-Key": api_key, "Content-Type": "application/json"},
        json={"profileId": profile_id, "timeout": timeout_min},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    save_cloud_session(data["id"], data["cdpUrl"], data.get("timeoutAt"))
    return data["cdpUrl"], data["id"]


def stop_cloud_browser():
    """Stop the cloud browser and clean up."""
    api_key = _get_api_key()
    saved = load_cloud_session()
    if saved and api_key:
        sid = saved.get("session_id")
        if sid:
            try:
                requests.patch(
                    f"{BROWSER_USE_API}/{sid}",
                    headers={"X-Browser-Use-API-Key": api_key, "Content-Type": "application/json"},
                    json={"action": "stop"},
                    timeout=15,
                )
            except Exception:
                pass
    clear_cloud_session()


# ── Login refresh (daily cron) ───────────────────────────────────────

def refresh_login(console=None):
    """Full login flow: start cloud browser, OTP, save cookies, stop browser.
    
    This is called by:
    - Daily login cron (proactive)
    - CLI commands when cookies are expired (reactive)
    - `ali login` command (manual)
    """
    from ali_cli.auth import browser_login
    from ali_cli.config import get_email

    def log(msg):
        if console:
            console.print(msg)
        else:
            print(msg)

    email = get_email()
    log("Starting cloud browser for login...")
    cdp_url, session_id = start_cloud_browser(timeout_min=10)
    log(f"  Session: {session_id[:12]}...")

    try:
        state, cookies = browser_login(cdp_url, email, console)
        save_state(state, cookies)
        update_login_status(True, method="browser_use_otp")
        log("✅ Login successful, cookies saved to ~/.ali-cli/state.json")
    finally:
        log("  Stopping cloud browser...")
        stop_cloud_browser()


# ── Cookie keepalive (cron every 8 hours) ────────────────────────────

def keepalive():
    """Refresh Alibaba session cookies using local browser.
    
    Loads saved cookies, navigates to alibaba.com, saves updated cookies.
    No Browser Use needed — just local Chromium.
    """
    from ali_cli.browser import BrowserManager
    
    state = load_state()
    if not state:
        print("No saved state — need full login first.")
        return False

    try:
        with BrowserManager(headless=True) as bm:
            # Navigate to a lightweight Alibaba page
            bm.page.goto("https://www.alibaba.com/", wait_until="domcontentloaded", timeout=20000)
            bm.page.wait_for_timeout(3000)
            
            # Check if still logged in
            logged_in = bm.page.evaluate("""() => {
                return document.body.innerText.includes('My Alibaba') || 
                       !document.body.innerText.includes('Sign in');
            }""")
            
            if logged_in:
                # Save refreshed cookies
                new_state = bm._context.storage_state()
                new_cookies = bm._context.cookies()
                save_state(new_state, new_cookies)
                print("✅ Cookies refreshed")
                return True
            else:
                print("⚠️ Cookies expired — need full login")
                return False
    except Exception as e:
        print(f"Keepalive failed: {e}")
        return False


# ── Convenience for backwards compat ─────────────────────────────────

def ensure_browser_session(timeout_min=120):
    """Legacy compat — returns (cdp_url, session_id) for cloud browser."""
    return start_cloud_browser(timeout_min)

def stop_browser_session():
    """Legacy compat — stops cloud browser."""
    stop_cloud_browser()

def ensure_logged_in_browser(cdp_url, target_url=None):
    """Legacy compat — connect to cloud browser and ensure logged in."""
    from ali_cli.browser import BrowserManager
    bm = BrowserManager(cdp_url=cdp_url)
    bm.start()
    url = target_url or "https://message.alibaba.com/message/messenger.htm"
    bm.page.goto(url, wait_until="domcontentloaded", timeout=30000)
    bm.page.wait_for_timeout(4000)
    if "login" in bm.page.url.lower():
        _do_otp_login(bm)
        bm.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        bm.page.wait_for_timeout(4000)
    return bm


def _do_otp_login(bm):
    """Perform OTP login flow on an already-open login page."""
    from ali_cli.auth import _paste_otp, get_gmail_service, get_fresh_otp
    from ali_cli.otp_watcher import read_latest_otp
    from ali_cli.config import get_email
    import re

    email = get_email()
    page = bm.page
    try:
        page.get_by_text("Sign in with a code").click()
        page.wait_for_timeout(1000)
    except Exception:
        pass
    try:
        page.locator("input[type='text']").first.fill(email)
        page.wait_for_timeout(400)
    except Exception:
        pass
    send_ts = time.time()
    try:
        page.get_by_role("button", name=re.compile("send code", re.I)).click()
        page.wait_for_timeout(1500)
    except Exception:
        pass
    otp = None
    for _ in range(12):
        otp = read_latest_otp(max_age_seconds=120)
        if otp:
            break
        time.sleep(5)
    if not otp:
        gmail = get_gmail_service()
        otp = get_fresh_otp(gmail, send_ts)
    if not otp:
        raise RuntimeError("Could not get OTP code.")
    _paste_otp(page, otp)
    page.wait_for_timeout(5000)
    if "login" in page.url.lower():
        raise RuntimeError("OTP login failed.")
