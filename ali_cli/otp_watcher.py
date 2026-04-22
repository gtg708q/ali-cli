"""OTP Watcher — polls Gmail for Alibaba verification codes and writes to file.

Usage:
    # Start in background before login:
    python3 -m ali_cli.otp_watcher &

    # Or from CLI:
    ali otp-watch &

    # The watcher writes the latest OTP to ALI_CLI_HOME/latest-otp.txt
    # (default: ~/.ali-cli/latest-otp.txt). The login flow reads from that
    # file instead of polling Gmail directly.
"""

import json
import os
import re
import sys
import time

from ali_cli.config import get_home, get_secrets_dir

OTP_FILE = get_home() / "latest-otp.txt"
POLL_INTERVAL = 5  # seconds


def get_gmail_service():
    """Build Gmail API service for the configured login account."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    secrets_dir = get_secrets_dir()
    creds_file = secrets_dir / "gmail-oauth-credentials.json"
    tokens_file = secrets_dir / "gmail-tokens.json"

    if not creds_file.exists() or not tokens_file.exists():
        print(f"Missing credentials: {creds_file} or {tokens_file}")
        print("See README.md for Gmail OAuth setup instructions.")
        sys.exit(1)

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


def poll_for_otp(gmail, since_ts, seen_ids=None):
    """Check for new OTP emails since timestamp. Returns (code, msg_id) or (None, None)."""
    if seen_ids is None:
        seen_ids = set()

    try:
        results = (
            gmail.users()
            .messages()
            .list(
                userId="me",
                q='subject:"Alibaba.com verification code" newer_than:30m',
                maxResults=3,
            )
            .execute()
        )
    except Exception as e:
        print(f"Gmail API error: {e}")
        return None, None

    for msg_meta in results.get("messages", []):
        if msg_meta["id"] in seen_ids:
            continue

        try:
            msg = (
                gmail.users()
                .messages()
                .get(userId="me", id=msg_meta["id"], format="metadata",
                     metadataHeaders=["Subject", "Date"])
                .execute()
            )
        except Exception:
            continue

        msg_ts = int(msg.get("internalDate", 0)) / 1000
        if msg_ts < since_ts:
            continue

        for h in msg["payload"]["headers"]:
            if h["name"] == "Subject":
                match = re.search(r"\b(\d{6})\b", h["value"])
                if match:
                    seen_ids.add(msg_meta["id"])
                    return match.group(1), msg_meta["id"]

    return None, None


def write_otp(code):
    """Write OTP code to file for the login flow to read."""
    OTP_FILE.parent.mkdir(parents=True, exist_ok=True)
    OTP_FILE.write_text(json.dumps({
        "code": code,
        "timestamp": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }))
    os.chmod(OTP_FILE, 0o600)


def read_latest_otp(max_age_seconds=300):
    """Read the latest OTP from file. Returns code or None if expired/missing."""
    if not OTP_FILE.exists():
        return None
    try:
        data = json.loads(OTP_FILE.read_text())
        age = time.time() - data.get("timestamp", 0)
        if age > max_age_seconds:
            return None
        return data.get("code")
    except Exception:
        return None


def main():
    """Run the OTP watcher — polls Gmail every 5 seconds."""
    print("🔍 OTP Watcher started — polling Gmail for Alibaba codes...")
    gmail = get_gmail_service()
    start_ts = time.time()
    seen_ids = set()
    last_code = None

    try:
        while True:
            code, msg_id = poll_for_otp(gmail, start_ts, seen_ids)
            if code and code != last_code:
                write_otp(code)
                print(f"✅ OTP captured: {code} (written to {OTP_FILE})")
                last_code = code
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\nOTP Watcher stopped.")


if __name__ == "__main__":
    main()
