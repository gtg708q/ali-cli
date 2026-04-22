# Security Policy

## Reporting a vulnerability

**Do not open a public issue for security concerns.** Use GitHub's
private vulnerability reporting:

<https://github.com/gtg708q/ali-cli/security/advisories/new>

That form sends the report privately to maintainers. We'll respond in
the advisory, coordinate a fix, and credit you in the release notes if
you'd like.

## What's in scope

This CLI handles credentials and authenticated browser sessions, so
we take those surfaces seriously. Issues worth reporting include:

- **Credential leakage.** Cases where `config.json`, `state.json`,
  `cookies.json`, `.env`, or the OAuth token files are written with
  loose permissions, logged to stdout/stderr, checked into git
  inadvertently, or exposed to another user on the same machine.
- **OAuth / token handling.** Gmail refresh-token exposure, missing
  revocation, token stored somewhere other than
  `ALI_CLI_HOME/secrets/`, plaintext transmission that should be over
  TLS.
- **Session handling.** Alibaba session cookies being shared with
  unrelated processes, cross-account cookie contamination, improperly
  scoped cookie storage.
- **Command injection.** CLI flags or config values that pass through
  to `subprocess`, `page.evaluate()`, or shell without proper
  sanitization.
- **Dependency vulnerabilities** in Playwright, click, Google OAuth
  libraries, or requests that materially affect this tool's security
  posture.

## What's out of scope

These aren't things this project can fix — take them upstream:

- Alibaba.com's own authentication, rate limiting, or scraping
  controls. (We comply with their ToS; we don't bypass their
  security.)
- Browser Use's cloud browser security. Report directly to
  <https://browser-use.com>.
- General Playwright browser-sandboxing issues. Report to the
  Playwright project.
- Anything requiring physical access to a user's machine. If an
  attacker has shell access to your laptop, your OAuth tokens
  aren't the worst of your problems.

## Response expectations

- Acknowledgment within **72 hours** of a report.
- A fix plan (or a dispute of scope) within **14 days**.
- Coordinated disclosure once the fix ships. Embargo timing is
  negotiable — we'd rather get it right than get it fast.

Because this is a single-maintainer project shared with a small peer
group, we don't offer a bounty, but we're happy to credit reporters.
