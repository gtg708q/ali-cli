"""One-time Gmail OAuth bootstrap for ali-cli.

Opens a browser window for Google's OAuth consent screen. Sign in as the
same Gmail account that your Alibaba buyer account uses — that's where
Alibaba will send the OTP verification codes that ali-cli needs to read.

Prerequisites:
  - ALI_CLI_HOME/secrets/gmail-oauth-credentials.json exists (Desktop OAuth
    client JSON downloaded from Google Cloud Console).
  - Your Gmail address is either listed as a test user on the OAuth consent
    screen, or the app is published.

Usage:
  python3 scripts/bootstrap_gmail.py

Writes tokens to ALI_CLI_HOME/secrets/gmail-tokens.json.
"""

import json
import os
import sys
from pathlib import Path

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    sys.stderr.write(
        "google-auth-oauthlib not installed. Run: pip install -e .\n"
    )
    sys.exit(1)


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> int:
    home = Path(os.environ.get("ALI_CLI_HOME", Path.home() / ".ali-cli"))
    secrets = home / "secrets"
    creds_file = secrets / "gmail-oauth-credentials.json"
    tokens_file = secrets / "gmail-tokens.json"

    if not creds_file.exists():
        sys.stderr.write(
            f"Missing {creds_file}\n\n"
            "Set up Google OAuth:\n"
            "  1. https://console.cloud.google.com/ → create project\n"
            "  2. Enable Gmail API\n"
            "  3. Credentials → Create OAuth client ID → Desktop app\n"
            "  4. Download the JSON, save it to the path above\n"
        )
        return 1

    secrets.mkdir(parents=True, exist_ok=True)

    print(f"Reading OAuth client from: {creds_file}")
    print("Opening browser for Google consent...")
    print("Sign in as the Gmail account your Alibaba buyer account uses.\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    tokens_file.write_text(
        json.dumps(
            {
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
            },
            indent=2,
        )
    )
    os.chmod(tokens_file, 0o600)

    print(f"\nSaved tokens to {tokens_file}")
    print(f"refresh_token present: {bool(creds.refresh_token)}")
    if not creds.refresh_token:
        print(
            "\n⚠️  No refresh_token was issued. This usually means you've"
            " previously consented; revoke the app at"
            " https://myaccount.google.com/permissions and re-run."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
