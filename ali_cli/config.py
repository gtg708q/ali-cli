"""Configuration and session management for Ali CLI.

All state lives under ALI_CLI_HOME (default: ~/.ali-cli/).

Layout:
  ~/.ali-cli/
    ├── config.json                   # User config (email, profile ID, timeouts)
    ├── .env                          # Optional: BROWSER_USE_API_KEY=...
    ├── state.json                    # Playwright storage_state (cookies + localStorage)
    ├── session.json                  # Legacy alias for state.json
    ├── cookies.json                  # Raw cookie list
    ├── browser-session.json          # Active Browser Use cloud session (if any)
    ├── login-status.json             # Last login timestamp + result
    ├── latest-otp.txt                # OTP code captured by otp_watcher
    └── secrets/
        ├── gmail-oauth-credentials.json  # Google Cloud OAuth client
        └── gmail-tokens.json             # Gmail refresh/access tokens
"""

import json
import os
from pathlib import Path


def get_home() -> Path:
    """Return the Ali CLI config root. Respects ALI_CLI_HOME env var."""
    return Path(os.environ.get("ALI_CLI_HOME", Path.home() / ".ali-cli"))


CONFIG_DIR = get_home()
CONFIG_FILE = CONFIG_DIR / "config.json"
# SESSION_FILE and STATE_FILE (in session_manager.py) point at the same file —
# Playwright storage_state serialized to JSON. Historically the code used
# two different names; unified here so every path reads/writes the same file.
SESSION_FILE = CONFIG_DIR / "state.json"
COOKIES_FILE = CONFIG_DIR / "cookies.json"
SECRETS_DIR = CONFIG_DIR / "secrets"
ENV_FILE = CONFIG_DIR / ".env"

DEFAULT_CONFIG = {
    "email": "",
    "headless": True,
    "timeout": 30000,
    "browser_use_api_key": "",
    "browser_use_profile_id": "",
}


def ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config():
    ensure_config_dir()
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            stored = json.load(f)
        return {**DEFAULT_CONFIG, **stored}
    return dict(DEFAULT_CONFIG)


def save_config(config):
    ensure_config_dir()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    os.chmod(CONFIG_FILE, 0o600)


def get_email(cli_override: str | None = None) -> str:
    """Resolve the Alibaba login email.

    Priority: CLI --email arg > config.json > ALI_EMAIL env var.
    Raises RuntimeError if no email is configured.
    """
    if cli_override:
        return cli_override
    config = load_config()
    email = config.get("email") or os.environ.get("ALI_EMAIL", "")
    if not email:
        raise RuntimeError(
            "No Alibaba login email configured. "
            "Run `ali config set-email you@example.com` or pass `--email`."
        )
    return email


def get_browser_use_api_key() -> str:
    """Load Browser Use API key from config, env, or ALI_CLI_HOME/.env."""
    config = load_config()
    api_key = config.get("browser_use_api_key", "")
    if api_key:
        return api_key
    api_key = os.environ.get("BROWSER_USE_API_KEY", "")
    if api_key:
        return api_key
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("BROWSER_USE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def get_browser_use_profile_id() -> str:
    """Load Browser Use profile ID from config or env. Required for login."""
    config = load_config()
    profile_id = config.get("browser_use_profile_id", "")
    if profile_id:
        return profile_id
    return os.environ.get("BROWSER_USE_PROFILE_ID", "")


def get_secrets_dir() -> Path:
    """Return secrets directory (ALI_CLI_HOME/secrets/). Creates it if missing."""
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    return SECRETS_DIR


def save_session(storage_state):
    """Save Playwright browser storage state."""
    ensure_config_dir()
    with open(SESSION_FILE, "w") as f:
        json.dump(storage_state, f, indent=2)
    os.chmod(SESSION_FILE, 0o600)


def load_session():
    if SESSION_FILE.exists():
        with open(SESSION_FILE) as f:
            return json.load(f)
    return None


def clear_session():
    for f in [SESSION_FILE, COOKIES_FILE]:
        if f.exists():
            f.unlink()


def save_cookies(cookies):
    """Save raw cookie list from browser context."""
    ensure_config_dir()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f, indent=2)
    os.chmod(COOKIES_FILE, 0o600)


def load_cookies():
    if COOKIES_FILE.exists():
        with open(COOKIES_FILE) as f:
            return json.load(f)
    return None


def session_exists():
    return SESSION_FILE.exists()
