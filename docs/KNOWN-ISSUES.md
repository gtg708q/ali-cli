# Known Issues & Workarounds

Patterns the CLI has learned the hard way. Read this before debugging a
browser-level failure — most "new" problems turn out to be one of these.

Append new entries here when you solve a novel failure mode; future-you
(and future contributors) will thank you.

---

## Auth / Session

**`not_authenticated` on `rfq.alibaba.com` despite valid cookies**
The cloud browser's profile does not persist sessions across stops. Inject
local `state.json` cookies into the cloud-browser context via
`context.add_cookies(load_state()["cookies"])` at the top of the RFQ flow.

**"Sign in" visible in header but auth actually works**
Misleading heuristic — the string "Sign in" appears in the public site nav
even when logged in. Check for `"My Alibaba"` or `"My store"` instead.
(See `ali_cli/auth.py::browser_login`.)

**`state.json` cookies expired**
Cookies last roughly 24 hours. Run `ali keepalive` every few hours to
extend, or `ali login` to mint a fresh set via OTP.

**Cloud-browser `_tb_token_` missing after close/reopen**
Closing all pages can flush in-memory cookies. Re-open the login page,
reload cookies, and re-navigate before reading the token.

---

## RFQ Posting

**"Please enter your detailed requirements" on submit**
Alibaba's AI generates the description text as a read-only preview div,
not as the textarea contents. You must click **Apply or modify** to
transfer the generated text into the textarea before submitting.

**AI generation percentage stuck / never completes**
Cloud browsers are slower than local. Wait up to 90 s. Detect completion
by the appearance of **Apply or modify** combined with the absence of a
`%` character, rather than a hard timer.

**Popup opens LinkedIn OAuth instead of `rfqForm`**
`.new-top-banner-bottom-action-btn` matches social share buttons too.
Use `page.get_by_text("Write RFQ details", exact=True)`.

**Form URL has empty `subject=`**
The textarea's React state isn't updated by `page.evaluate()` with a
native setter. Use Playwright's `locator.fill()` instead.

---

## Messages & Downloads

**`ali download` returns zero files**
`fetch()` from `message.alibaba.com` to `clouddisk.alibaba.com` is blocked
by CORS. Open the file URL in `context.new_page()` — the new page shares
cookies with the context and bypasses CORS. See
`ali_cli/browser.py::download_messages_media`.

**`get_conversations()` returns empty in headless mode**
`window.__conversationListFullData__` isn't always populated in headless
Chromium. The CLI falls back to the unread API automatically (v0.3+).

**`send_message` doesn't deliver**
The DOM textarea needs the React native setter plus `input`/`change`
events. Either a `.click()` on Send or an Enter keydown works.

---

## Browser & Playwright

**`set_input_files()` silently drops the upload**
The file input only accepts uploads when the session is authenticated.
Unauthenticated sessions return zero API calls with no error. Always
assert logged-in before attempting file upload.

**`storage_state()` throws "Execution context was destroyed"**
Navigation happened during save. Two defenses: call
`page.wait_for_load_state("networkidle")` first, and fall back to
`context.cookies()` if `storage_state()` still raises. See
`ali_cli/auth.py::browser_login`.

**Local headless browser hits baxia captcha on `rfq.alibaba.com`**
Expected. RFQ posting requires the cloud browser with residential IP.
All other operations (messenger, RFQ reads, quotes) work fine against
local headless Chromium with saved cookies.
