# Changelog

All notable changes to ali-cli are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-04-22

Initial public release.

### Highlights
- **OTP-based Alibaba login** via a Browser Use cloud browser. Cookies
  persist to `~/.ali-cli/state.json` so subsequent commands run free in
  local headless Chromium.
- **Messenger** — list conversations, read threads (by index or fuzzy
  supplier name), send messages / images / files, download media.
- **RFQs** — list, detail, per-RFQ quote pricing, and AI-assisted posting
  with file attachments.
- **Monitor** — one-session full check (messages + RFQs + quotes) for
  cron-driven automation.
- **Observability & self-healing** — step-level run tracing, error
  fingerprinting, and auto-recovery for known Alibaba quirks (session
  expired, `storage_state` races, CORS-blocked downloads, headless DOM
  gaps, captcha handoff).
- **Doctor suite** — `ali doctor` self-tests, `--analyze` for 7-day
  error-pattern reporting, `--heal` for auto-recovery sweeps, and
  `--log-issue` to capture novel failures to a local log.
- **Standalone config** under `~/.ali-cli/` (override with
  `ALI_CLI_HOME`), with an `ali config` subcommand group for self-serve
  setup.
