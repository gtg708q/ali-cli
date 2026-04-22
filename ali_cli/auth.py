"""Authentication — OTP-based login via Browser Use cloud browser.

OTP Notes:
- Alibaba OTP codes appear to remain valid for ~2-3 hours after receipt.
- Browser Use profile cookies expire after ~24 hours but OTP is still needed.
- If more than 5 OTPs are sent in a single session, increase wait time between
  attempts to avoid rate-limiting.
"""

import json
import os
import re
import time
from pathlib import Path

import requests

from ali_cli.config import get_secrets_dir

BROWSER_USE_API = "https://api.browser-use.com/api/v2/browsers"

# Track OTP send attempts within a session
_otp_send_count = 0
_OTP_THROTTLE_THRESHOLD = 5
_OTP_THROTTLE_DELAY = 30  # seconds to wait between attempts after threshold


def get_gmail_service():
    """Build Gmail API service for the configured login account.

    Requires OAuth credentials + tokens under ALI_CLI_HOME/secrets/.
    See README.md for setup.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    secrets_dir = get_secrets_dir()
    creds_file = secrets_dir / "gmail-oauth-credentials.json"
    tokens_file = secrets_dir / "gmail-tokens.json"

    if not creds_file.exists() or not tokens_file.exists():
        raise FileNotFoundError(
            f"Gmail OAuth not configured. Expected:\n"
            f"  {creds_file}\n  {tokens_file}\n"
            f"See README.md for Gmail OAuth setup instructions."
        )

    creds_data = json.load(open(creds_file))["installed"]
    tokens = json.load(open(tokens_file))

    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds_data["client_id"],
        client_secret=creds_data["client_secret"],
    )

    if creds.expired or not creds.token:
        creds.refresh(Request())
        tokens["access_token"] = creds.token
        with open(tokens_file, "w") as f:
            json.dump(tokens, f)

    return build("gmail", "v1", credentials=creds)


def get_fresh_otp(gmail_service, after_ts, used_ids=None):
    """Fetch OTP code from Gmail, only emails received after after_ts."""
    if used_ids is None:
        used_ids = set()

    for attempt in range(24):  # up to 2 min
        results = (
            gmail_service.users()
            .messages()
            .list(
                userId="me",
                q='subject:"Alibaba.com verification code"',
                maxResults=5,
            )
            .execute()
        )

        for msg_meta in results.get("messages", []):
            if msg_meta["id"] in used_ids:
                continue
            msg = (
                gmail_service.users()
                .messages()
                .get(userId="me", id=msg_meta["id"])
                .execute()
            )
            msg_ts = int(msg.get("internalDate", 0)) / 1000
            if msg_ts < after_ts:
                continue

            for h in msg["payload"]["headers"]:
                if h["name"] == "Subject":
                    match = re.search(r"\b(\d{6})\b", h["value"])
                    if match:
                        used_ids.add(msg_meta["id"])
                        return match.group(1)

        if attempt < 23:
            time.sleep(5)

    return None


def start_browser_session(api_key, profile_id):
    """Start a Browser Use cloud browser session."""
    resp = requests.post(
        BROWSER_USE_API,
        headers={
            "X-Browser-Use-API-Key": api_key,
            "Content-Type": "application/json",
        },
        json={"profileId": profile_id},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["id"], data["cdpUrl"]


def stop_browser_session(api_key, session_id):
    """Stop a Browser Use cloud browser session."""
    try:
        resp = requests.patch(
            f"{BROWSER_USE_API}/{session_id}",
            headers={
                "X-Browser-Use-API-Key": api_key,
                "Content-Type": "application/json",
            },
            json={"action": "stop"},
            timeout=15,
        )
        return resp.json()
    except Exception:
        return None


def _paste_otp(page, otp_code):
    """Paste OTP code into React inputs using ClipboardEvent (confirmed working).

    Alibaba's React OTP inputs ignore .fill() — clipboard paste is the only
    reliable method.
    """
    page.evaluate(
        """(code) => {
            const firstInput = document.querySelectorAll('input[type="text"]')[0];
            if (!firstInput) throw new Error('No OTP input found');
            firstInput.focus();
            const pasteData = new DataTransfer();
            pasteData.setData('text/plain', code);
            const pasteEvent = new ClipboardEvent('paste', {
                bubbles: true,
                cancelable: true,
                clipboardData: pasteData
            });
            firstInput.dispatchEvent(pasteEvent);
        }""",
        otp_code,
    )


def browser_login(cdp_url, email, console=None):
    """
    Login to Alibaba via code-based OTP flow in a CDP browser.
    Returns (storage_state, cookies) on success, raises on failure.
    """
    from playwright.sync_api import sync_playwright

    def log(msg):
        if console:
            console.print(msg)
        else:
            print(msg)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0]

        # Close existing pages
        for pg in context.pages:
            try:
                pg.close()
            except Exception:
                pass

        page = context.new_page()

        # FIRST: Check if profile cookies are still valid by visiting alibaba.com
        log("  Checking if profile cookies are still valid...")
        page.goto(
            "https://www.alibaba.com/",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        page.wait_for_timeout(3000)

        # Look for signs of being logged in. Only accept strong signals:
        # "My Alibaba" / "My store" are logged-in-only. "Buyer Central" and
        # "Sign in" both appear in the public site nav so they're useless.
        is_logged_in = page.evaluate(
            """() => {
                const text = document.body.innerText;
                return text.includes('My Alibaba') || text.includes('My store');
            }"""
        )

        if is_logged_in:
            log("  ✅ Already logged in from profile cookies! No OTP needed.")
            # Navigate to messenger to seed all domain cookies
            page.goto(
                "https://message.alibaba.com/message/messenger.htm",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            page.wait_for_timeout(3000)
            state = context.storage_state()
            cookies = context.cookies()
            page.close()
            return state, cookies

        log("  Profile cookies expired. Starting OTP login flow...")
        
        # Navigate to login page
        page.goto(
            "https://login.alibaba.com/newlogin/icbuLogin.htm",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        page.wait_for_timeout(2000)

        # Double-check we're actually on login page
        if "login" not in page.url.lower():
            log("  Already logged in from profile cookies!")
            state = context.storage_state()
            cookies = context.cookies()
            page.close()
            return state, cookies

        # Click "Sign in with a code"
        try:
            page.get_by_text("Sign in with a code").click()
            page.wait_for_timeout(1000)
        except Exception:
            log("  [yellow]Could not find 'Sign in with a code' button[/yellow]")

        # Fill email
        email_input = page.locator("input[type='text']").first
        email_input.fill(email)
        page.wait_for_timeout(400)

        # Click Send Code (with throttling after many attempts)
        global _otp_send_count
        _otp_send_count += 1
        if _otp_send_count > _OTP_THROTTLE_THRESHOLD:
            delay = _OTP_THROTTLE_DELAY * (_otp_send_count - _OTP_THROTTLE_THRESHOLD)
            log(f"  [yellow]OTP attempt #{_otp_send_count} — throttling {delay}s[/yellow]")
            time.sleep(delay)

        send_ts = time.time()
        try:
            page.get_by_role("button", name=re.compile("send code", re.I)).click()
            page.wait_for_timeout(1500)
            log(f"  OTP code sent (attempt #{_otp_send_count}), waiting for email...")
        except Exception:
            log("  [yellow]Could not find Send Code button[/yellow]")

        # Fetch OTP — check file first (from otp_watcher), then Gmail
        from ali_cli.otp_watcher import read_latest_otp
        
        otp = None
        # Check if OTP watcher already caught a fresh code
        for check in range(6):  # 30 seconds of checking file
            file_otp = read_latest_otp(max_age_seconds=120)
            if file_otp:
                otp = file_otp
                log(f"  OTP from watcher file: {otp}")
                break
            time.sleep(5)
        
        # Fallback: poll Gmail directly
        if not otp:
            log("  No OTP in watcher file, polling Gmail...")
            gmail = get_gmail_service()
            otp = get_fresh_otp(gmail, send_ts)

        if not otp:
            page.close()
            raise RuntimeError("No OTP received from Gmail within timeout")

        log(f"  OTP received: {otp}")

        # Paste OTP using clipboard event (confirmed working with React inputs)
        _paste_otp(page, otp)
        page.wait_for_timeout(500)

        # Submit
        try:
            page.get_by_role(
                "button", name=re.compile(r"sign in|log in|continue|verify", re.I)
            ).first.click()
        except Exception:
            page.keyboard.press("Enter")

        page.wait_for_timeout(5000)

        if "login" in page.url.lower():
            page.close()
            raise RuntimeError(f"Login failed — still on login page: {page.url}")

        log("  Login successful!")

        # Navigate to messenger to seed all domain cookies
        page.goto(
            "https://message.alibaba.com/message/messenger.htm",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        page.wait_for_timeout(5000)

        # Extract ctoken from page (while it's still alive)
        ctoken = None
        try:
            ctoken = page.evaluate(
                """() => {
                const ct = document.cookie.split(';').find(c => c.trim().startsWith('ctoken='));
                return ct ? ct.split('=')[1].trim() : null;
            }"""
            )
        except Exception:
            pass

        # Wait for network idle so any background navigation settles before
        # we try to serialize storage_state. Retry once if the context is
        # still mid-navigation (see docs/KNOWN-ISSUES.md).
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        def _grab_state():
            cookies_local = context.cookies()
            try:
                state_local = context.storage_state()
            except Exception:
                # Fallback: reconstruct a minimal storage_state from cookies only.
                state_local = {"cookies": cookies_local, "origins": []}
            return state_local, cookies_local

        try:
            state, cookies = _grab_state()
        except Exception:
            page.wait_for_timeout(3000)
            state, cookies = _grab_state()

        # Add ctoken to state metadata
        if ctoken:
            state["_ctoken"] = ctoken

        page.close()
        return state, cookies
