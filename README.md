# ali-cli — Alibaba.com Buyer Portal CLI

CLI tool for automating Alibaba.com buyer-side operations: conversations, messages, RFQs, quotes, and supplier communication. Built on Playwright with session persistence, a cloud browser for OTP login, and an error/recovery layer suitable for cron-driven automation.

> ⚠️ Alibaba's terms of service restrict automated access. This tool drives
> an authenticated browser session for **your own** buyer account. You are
> responsible for compliance with Alibaba's ToS and with any rate limits
> or scraping prohibitions that apply to your use case.

---

## How it works

- **Login** runs once in a [Browser Use](https://browser-use.com) cloud browser (OTP emailed to your Gmail, fetched via Gmail API, pasted automatically). Cookies are saved locally.
- **Everything else** runs in a local headless Chromium with those saved cookies — free, fast, no cloud dependency.
- **All Alibaba API calls** go through `page.evaluate()` in browser context. Direct HTTP returns 503 due to Alibaba's signed request scheme.

```
┌─────────────────────────────────────┐
│  DAILY LOGIN (Browser Use cloud)    │
│  → Saves cookies to state.json      │
└─────────────────┬───────────────────┘
                  ↓
┌─────────────────────────────────────┐
│  ALL OPERATIONS (local Chromium)    │
│  → Loads state.json cookies         │
│  → page.evaluate() for API calls    │
└─────────────────────────────────────┘
```

---

## Prerequisites

| | |
|---|---|
| **Python 3.10+** | Required for the CLI + Playwright |
| **An Alibaba.com buyer account** | The Alibaba account's login email **must match** the Gmail inbox in step 4 — that's where Alibaba sends the OTP codes |
| **A Gmail account** | Same address as your Alibaba login. The CLI reads OTP emails from this inbox. |
| **A Browser Use account** | <https://browser-use.com> — used only for the OTP login step (~$0.06 per login). Get an **API key**, then create a named **profile** in the dashboard. That profile will store your Alibaba session cookies between runs. |
| **A Google Cloud project** | With the Gmail API enabled. Used to mint an OAuth refresh token so the CLI can read OTP emails. |

---

## Install

```bash
git clone https://github.com/gtg708q/ali-cli.git
cd ali-cli
pip install -e .
playwright install chromium
```

---

## First-run setup (5 minutes)

All state lives under `~/.ali-cli/` (override with the `ALI_CLI_HOME` env var).

### Step 1 — Create a Browser Use profile

1. Sign in at <https://browser-use.com>, go to the dashboard.
2. Create a **Browser Profile** (name it `alibaba` or similar). Copy the profile ID.
3. Copy your **API key** from the dashboard's API section.

### Step 2 — Configure ali-cli

```bash
ali config set-email you@example.com               # Your Alibaba login email
ali config set-api-key bu_xxxxxxxxxxxxxxx          # Browser Use API key
ali config set-profile-id xxxxxxxx-xxxx-xxxx-...   # Browser Use profile ID
```

### Step 3 — Google Cloud OAuth client

1. Go to <https://console.cloud.google.com/>, pick or create a project.
2. **APIs & Services → Library → Gmail API → Enable**.
3. **APIs & Services → OAuth consent screen** — pick **External**, fill in the required fields, and under **Test users** add your own Gmail address. (The only scope you need is `gmail.readonly`, added automatically by the bootstrap script.)
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**. Type: **Desktop app**.
5. Click **Download JSON** on the newly created client.
6. Save it exactly here: `~/.ali-cli/secrets/gmail-oauth-credentials.json`

### Step 4 — Mint Gmail tokens (one-time)

```bash
python3 scripts/bootstrap_gmail.py
```

A browser window opens. Sign in as the Gmail address matching your Alibaba login and approve read-only Gmail access. The script writes `~/.ali-cli/secrets/gmail-tokens.json`.

### Step 5 — Log in to Alibaba

```bash
ali login
```

**First run:** your Browser Use profile has no Alibaba cookies yet, so this always triggers an OTP. The CLI starts the cloud browser, fills your email, asks Alibaba to send a code, pulls it from Gmail, pastes it, and saves the session to `~/.ali-cli/state.json`. The cloud browser stops as soon as login finishes.

**Subsequent runs:** if the profile still has a valid Alibaba session (cookies last ~24 hours), login skips OTP entirely. Run `ali keepalive` daily — or on a cron — to refresh cookies without an OTP round-trip.

Confirm with:

```bash
ali status --json
# {"logged_in": true, "unread_count": 42, ...}
```

---

## Config reference

### Config file

`~/.ali-cli/config.json`:

```json
{
  "email": "you@example.com",
  "headless": true,
  "timeout": 30000,
  "browser_use_api_key": "bu_xxxxxxxxxxxxxxx",
  "browser_use_profile_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

`ali config show` prints the current config with the API key redacted.

### Environment variable overrides

Any of these override config.json:

| Var | Purpose |
|---|---|
| `ALI_CLI_HOME` | Config root (default `~/.ali-cli/`) |
| `ALI_EMAIL` | Alibaba login email |
| `BROWSER_USE_API_KEY` | Browser Use API key |
| `BROWSER_USE_PROFILE_ID` | Browser Use profile ID |

You can also drop `BROWSER_USE_API_KEY=...` into `~/.ali-cli/.env`.

---

## Commands

### Session
```bash
ali login                          # OTP login via cloud browser (once per day-ish)
ali logout                         # Clear session + stop cloud browser
ali status [--json]                # Logged in? unread count?
ali health [--quick] [--json]      # Cookie age, API reachability
ali keepalive                      # Refresh cookies without full login
ali browser start|stop|status      # Manage cloud browser session
```

### Config
```bash
ali config show                    # Print current config
ali config set-email EMAIL
ali config set-api-key KEY
ali config set-profile-id ID
ali config path                    # Print ALI_CLI_HOME
```

### Messaging
```bash
ali messages [--unread] [--json]
ali conversations [--unread]       # Alias with extra company info
ali read <n> [--count 20] [--json]
ali read --name "acme"   [--json]  # Fuzzy match by supplier/company name
ali send <n> "text"
ali send-image <n> /path/to/img [--caption "..."]
ali send-file <n> /path/to/file
ali download <n> [--type image]
```

### RFQs
```bash
ali rfqs [--json]                  # All RFQs + quote counts
ali rfq <id> [--json]              # Detail + quote list
ali rfq-quotes <id> [--json]       # Pricing from comparison page
ali post-rfq --subject "..." --quantity 10000 [--attach rfq.xlsx] [--dry-run]
```

### Monitoring
```bash
ali monitor [--json]               # Full check in ONE browser session (~20s)
```

### Observability & self-healing
```bash
ali logs                           # Latest run step trace
ali logs --errors                  # Recent errors only
ali logs --run <id>                # Specific run trace
ali logs --since 24                # Last N hours
ali logs --command monitor         # Filter by command
ali doctor                         # Run the full self-test suite
ali doctor --fast                  # Quick cookie-freshness check only
ali doctor --analyze               # 7-day error-pattern + skill-health report
ali doctor --heal                  # Attempt auto-recovery for known patterns
ali doctor --log-issue "desc"      # Append a local issue to ~/.ali-cli/issues.md
```

See the next section for what's actually happening under those commands.

---

## How self-healing works

Alibaba's DOM and APIs drift. A browser-automation CLI that only runs a
happy path becomes useless fast. ali-cli is instrumented end-to-end so it
can diagnose itself, fix known failure modes without human intervention,
and accumulate institutional memory.

### 1. Every step is traced

Each browser operation runs inside a `step()` context manager that writes
a structured event to `~/.ali-cli/run.jsonl` with command, step name,
status, duration, and an opaque `run_id`. Failures additionally land in
`errors.jsonl` with the exception text, a hint, and a stack location.

```bash
ali logs --run 03347241    # reconstruct any run after the fact
ali logs --errors          # just show failures
```

You never have to guess what the CLI did — the trace is always on disk.

### 2. Errors are fingerprinted

`ali_cli/recovery.py` holds a table of known failure patterns (regex
signatures + a `recovery_action` + a user-facing hint), seeded from the
patterns documented in [`docs/KNOWN-ISSUES.md`](docs/KNOWN-ISSUES.md).

When an exception fires, its message is matched against that table. If
there's a hit, ali-cli knows:

- what caused it (`session_expired`, `baxia_captcha`,
  `context_destroyed`, `rfq_ai_stuck`, `download_cors`, …),
- whether it's auto-recoverable,
- which action to take (e.g. `relogin`, `use_cookies_instead`,
  `click_apply_modify`, `fallback_unread_api`),
- and what hint to show the user if recovery can't run unattended.

### 3. Known patterns self-recover

For recoverable patterns the recovery action runs immediately. Examples:

| Pattern | Recovery |
|---|---|
| `session_expired` | triggers the OTP relogin flow |
| `context_destroyed` (storage_state race) | falls back to `context.cookies()` |
| `download_cors` (messenger→clouddisk) | opens URL in `context.new_page()` instead of `fetch()` |
| `conversations_empty` (headless DOM race) | falls back to the unread API |
| `rfq_ai_stuck` (AI preview not in textarea) | clicks "Apply or modify" |

Every recovery attempt is logged to `recovery.jsonl` with its outcome, so
`ali doctor --analyze` can report which patterns are recurring and which
are actually being fixed.

### 4. Doctor ties it together

```bash
ali doctor               # health + messages + RFQs + post-rfq dry-run
ali doctor --analyze     # 7-day summary: error pattern counts, recovery
                         #   success rates, skill-run health
ali doctor --heal        # loop through auto-recoverable patterns
ali doctor --fix         # write REPAIR-NEEDED.md with the failing tests
                         #   and known-issue context (hand-off for a
                         #   coding agent or a human)
```

Exit codes from `ali doctor` are wired for cron branching (see the Exit
codes table below).

### 5. Novel issues get captured

When you hit a failure that isn't in the table yet:

```bash
ali doctor --log-issue "rfq-posting — popup opens LinkedIn OAuth instead of rfqForm"
```

Appends an entry to `~/.ali-cli/issues.md` (dated, numbered, with current
command context). That file is your machine's local memory — it persists
across CLI upgrades and you can skim it before debugging a new failure.

If you diagnose the root cause and want to make it permanent, add a new
entry to `docs/KNOWN-ISSUES.md` and a matching pattern to
`ali_cli/recovery.py::KNOWN_PATTERNS`, then open a PR. That's how the
recovery table grows.

---

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error |
| 2 | Session expired (run `ali login`) |
| 3 | Recovery attempted but retry needed |
| 4 | Infrastructure error |

Useful for cron branching.

---

## Files written

Under `ALI_CLI_HOME` (default `~/.ali-cli/`):

```
config.json               User config
.env                      Optional env overrides (permission 0600)
state.json                Playwright storage_state (cookies + localStorage)
session.json              Legacy alias for state.json
cookies.json              Raw cookie list
browser-session.json      Active Browser Use session, if any
login-status.json         Last login timestamp
latest-otp.txt            OTP code captured by the watcher
run.jsonl                 Step-level trace of every CLI run
errors.jsonl              Errors only
skill-reports.jsonl       External skill run reports
secrets/                  Gmail OAuth + tokens
```

---

## Architecture

- `ali_cli/browser.py` — Playwright browser manager (local headless + cloud CDP)
- `ali_cli/messenger.py` — Conversation list, message read/send
- `ali_cli/rfq.py` — RFQ listing, detail, quote extraction
- `ali_cli/rfq_post.py` — RFQ posting (requires cloud browser)
- `ali_cli/monitor.py` — Single-session full monitoring
- `ali_cli/session_manager.py` — Cloud browser lifecycle
- `ali_cli/otp_watcher.py` — Gmail OTP auto-fetch
- `ali_cli/doctor.py` — Self-test suite
- `ali_cli/errors.py` — Step-level logging, `AliError`, `step()` context
- `ali_cli/recovery.py` — Known pattern fingerprinting + auto-recovery
- `ali_cli/config.py` — Config/paths
- `ali_cli/auth.py` — Gmail API + OTP paste
- `ali_cli/cli.py` — Click entry point

See [`docs/SPEC.md`](docs/SPEC.md) and [`docs/RECON.md`](docs/RECON.md) for
Alibaba API notes, and [`docs/KNOWN-ISSUES.md`](docs/KNOWN-ISSUES.md) for
recurring failure modes.

---

## Troubleshooting

**`ali login` fails with "No Browser Use API key configured"**
You skipped step 2. Run `ali config set-api-key bu_...`.

**`ali login` hangs for 30+ seconds then times out**
Cold cloud browsers can be slow. Re-run — session is cached and starts instantly the second time. If it keeps failing, `ali browser stop` to kill any zombie session, then retry.

**"No OTP received from Gmail within timeout"**
Three likely causes:
1. Your OAuth consent screen has you listed as a *test user*, but you're signed in with a different Google account. Re-run `python3 scripts/bootstrap_gmail.py` from the correct account.
2. Alibaba didn't actually send the code (rate limit — they throttle after several attempts in a short window). Wait 30–60 minutes.
3. The OTP email went to spam. Check `sourcing@yourdomain.com` manually; once you see "Alibaba.com verification code: XXXXXX", it'll also show up to the CLI.

**`ali status` returns `"session_expired"` right after `ali login`**
The Browser Use profile detected "logged in on alibaba.com" but the session isn't strong enough for messenger. This should auto-fall-through to OTP now — if you see it, clear state and force OTP:
```bash
rm ~/.ali-cli/state.json ~/.ali-cli/cookies.json
ali browser stop
ali login
```

**`bootstrap_gmail.py` opens a consent screen that says "Access blocked: ali-cli has not completed the Google verification process"**
Your OAuth consent screen is in *Testing* mode and your Gmail isn't listed as a test user. Google Cloud Console → APIs & Services → OAuth consent screen → Test users → add your address.

**`bootstrap_gmail.py` completes but `refresh_token present: False`**
Google only issues a refresh token on first consent. Revoke the app at <https://myaccount.google.com/permissions> and re-run the script.

**Alibaba asks for a captcha in the cloud browser**
A human sometimes has to solve one. Visit <https://browser-use.com>, find your active browser session, click the live view, solve the captcha, then re-run `ali login`. Happens rarely.

**Cron: `ali monitor --json` hangs forever**
Add a 60s timeout at the cron level (`timeout 60 ali monitor --json`). If a browser operation wedges, the CLI can hang.

---

## License

MIT — see [LICENSE](LICENSE).

## Contributing

This started as an internal tool and was open-sourced to share with peers. Issues and PRs welcome — expect Alibaba to change its DOM/APIs from time to time; [`docs/KNOWN-ISSUES.md`](docs/KNOWN-ISSUES.md) records the recurring patterns.
