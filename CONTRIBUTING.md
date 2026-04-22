# Contributing

Thanks for considering a contribution. This CLI stays useful only if it
keeps up with Alibaba's DOM/API changes, so contributions that capture
a new failure mode or fix a broken selector are especially valuable.

## Dev setup

```bash
git clone https://github.com/gtg708q/ali-cli.git
cd ali-cli
make dev                 # installs runtime + dev deps (pytest, ruff)
make playwright          # installs Chromium for Playwright
```

Complete the first-run setup in [README → First-run setup](README.md#first-run-setup-5-minutes)
to get a working `ali login` before you run tests.

## Running tests

```bash
make test
```

The end-to-end suite (`tests/test_e2e.py`) drives the installed `ali`
CLI via subprocess. It requires a live Alibaba session, so run
`ali login` first. Each test has a 60-second timeout.

There are no offline/mocked tests yet — contributions welcome, but keep
in mind that Alibaba's real response shape is the interesting thing
most of these tests cover.

## Lint and format

```bash
make lint    # ruff check
make fmt     # ruff --fix + ruff format
```

Keep imports sorted (ruff handles it). Line length limit is soft;
readability wins.

## The highest-value contribution: new recovery patterns

Alibaba changes selectors and response shapes periodically. When the
CLI hits a new failure, the ideal contribution is to **turn that
failure into an auto-recoverable pattern**. The pipeline:

1. **Hit a failure.** Grab the run ID:
   ```bash
   ali logs --errors --since 1
   ali logs --run <id>
   ```

2. **Diagnose the root cause.** Read the step trace. If the failure is
   reproducible, figure out what changed on Alibaba's side.

3. **Document it** in [`docs/KNOWN-ISSUES.md`](docs/KNOWN-ISSUES.md).
   Add a short entry under the relevant category (Auth, RFQ Posting,
   Messages & Downloads, Browser & Playwright). Keep the tone terse:
   symptom, cause, fix.

4. **Add a recovery pattern** in
   [`ali_cli/recovery.py`](ali_cli/recovery.py). New entries go into
   `KNOWN_PATTERNS` with:
   - `id` — snake-case identifier (`session_expired`, `baxia_captcha`).
   - `match_patterns` — list of strings or regexes to fingerprint the
     error message.
   - `severity` — `critical`, `medium`, or `low`.
   - `auto_recoverable` — `True` if the recovery action can run
     headless; `False` if it needs a human.
   - `recovery_action` — snake-case action name.
   - `description`, `hint` — user-facing copy.

5. **Implement the recovery action** in `attempt_recovery()` if it's a
   new action name. If it reuses an existing action (e.g., `relogin`),
   you're done after step 4.

6. **Add a test** in `tests/test_e2e.py` if the failure mode is
   reproducible against live Alibaba.

7. **Update the CHANGELOG.** Add an entry under the `## [Unreleased]`
   heading (create one if it doesn't exist) describing the new pattern.

## Non-recovery contributions

Also welcome:

- **New CLI commands or flags.** Keep the flat-surface pattern: flags
  in, `--json` out, meaningful exit codes. Add a help docstring.
- **Cron-ergonomics improvements.** Anything that makes the CLI more
  reliable inside a cron (timeouts, retries, `--json` completeness).
- **Documentation.** Specifically: concrete troubleshooting entries in
  the README's Troubleshooting section that name the exact error
  message a user will see.

Please **avoid**:

- Adding a database, queue, or background service. This tool is
  intentionally a stateless CLI with a file-based config root.
- Pulling in your internal credentials, supplier names, or buyer IDs
  into examples. Use `<buyer_id>`, `acme`, or `Example Seller Co.`.
- Adding features that require an LLM at runtime. The CLI should stay
  callable from any language, any cron, no API key beyond Browser Use
  + Gmail.

## Pull request checklist

- [ ] `make lint` and `make fmt` pass cleanly.
- [ ] `make test` passes against a live session (or you've explained
  why it can't in the PR description).
- [ ] `CHANGELOG.md` updated.
- [ ] If you added a recovery pattern: entry in
  `docs/KNOWN-ISSUES.md` + pattern in `ali_cli/recovery.py`.
- [ ] No credentials, personal IDs, or real supplier names in the
  diff. Quick grep: `grep -rniE '(bu_|sk-|[a-z0-9._-]+@[a-z0-9.-]+\.co|[0-9]{12})' <your-files>`.
- [ ] Commit messages explain *why*, not just *what*.

## Security issues

Don't open a public issue for security. See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed
under the MIT License.
