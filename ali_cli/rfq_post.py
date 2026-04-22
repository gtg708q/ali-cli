"""RFQ Posting — browser-automated flow for posting new RFQs on Alibaba.

Flow (all via cloud browser, fully automated):
  1. Upload file → filebroker.alibaba.com/x/upload
  2. Validate file → rfq.alibaba.com/rfq/rfq_annex_check_ajax.do
  3. Save file list → rfqposting.alibaba.com/rfq/ajax/multimodal/saveFileList.do
  4. Navigate to form → rfq.alibaba.com/rfq/rfqForm.htm (opens as popup)
  5. AI generates RFQ fields (polled via loadResponse.do)
  6. Fill quantity, accept terms
  7. Submit → "Post request" button
  8. Return RFQ ID from success page/redirect

Note: This REQUIRES a cloud browser session because rfq.alibaba.com
uses signed requests and React state that cannot be replicated via HTTP.
"""

import json
import os
import re
import time
import urllib.parse
from pathlib import Path

from ali_cli.errors import start_run, step, log_step, log_error


def _do_inline_login(page, log):
    """Perform OTP login on the current page without opening a new one.
    
    Navigates to the login page, does the OTP flow, then returns.
    The caller should then seed cookies across domains.
    """
    import re as _re
    from ali_cli.auth import get_gmail_service, get_fresh_otp, _paste_otp
    from ali_cli.otp_watcher import read_latest_otp
    from ali_cli.config import get_email

    email = get_email()
    page.goto("https://login.alibaba.com/newlogin/icbuLogin.htm",
              wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)

    # Click "Sign in with a code"
    page.click("button:has-text('Sign in with a code')", timeout=10000)
    page.wait_for_timeout(2000)

    # Fill email
    page.locator("input[type='text']:visible").first.fill(email)
    page.wait_for_timeout(500)

    # Send code
    send_ts = time.time()
    page.click("button:has-text('Send code')", timeout=10000)
    log("  OTP sent, waiting for code...")

    # Get OTP
    gmail = get_gmail_service()
    otp = None
    for i in range(15):
        otp = read_latest_otp(max_age_seconds=90)
        if otp and time.time() - send_ts > 5:
            break
        if i >= 2:
            otp = get_fresh_otp(gmail, send_ts)
            if otp:
                break
        time.sleep(5)

    if not otp:
        raise RuntimeError("No OTP received from Gmail")

    log(f"  OTP received: {otp}")
    _paste_otp(page, otp)
    page.wait_for_timeout(1000)

    try:
        page.click("button:has-text('Sign in')", timeout=5000)
    except Exception:
        page.keyboard.press("Enter")

    page.wait_for_timeout(15000)

    if "login" in page.url.lower():
        raise RuntimeError(f"Login failed — still on {page.url}")

    log("  ✅ Login successful")


def post_rfq(cdp_url: str, subject: str, quantity: int = 0, unit: str = "pieces",
             attachment: str = None, description: str = None, auto_generate: bool = True,
             dry_run: bool = False, console=None):
    """Post a new RFQ on Alibaba.com via cloud browser.

    Args:
        cdp_url: CDP URL for the cloud browser session.
        subject: RFQ subject/description text (sent to AI generation).
        quantity: Sourcing quantity (e.g. 10000).
        unit: Quantity unit (default: "pieces").
        attachment: Path to file to attach (xlsx, pdf, etc.).
        description: Override the AI-generated description (optional).
        auto_generate: Let Alibaba AI generate the RFQ details (default: True).
        dry_run: If True, stop before final submit and return form data.
        console: Rich console for logging (optional).

    Returns:
        dict with keys: success, rfq_id (if available), form_data, url
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

    def log(msg):
        if console:
            console.print(msg)
        else:
            print(msg)

    run_id = start_run("post-rfq", {"subject": subject[:80], "quantity": quantity})
    result = {"success": False, "rfq_id": None, "form_data": {}, "url": "", "run_id": run_id}

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0]

        # Inject our saved local session cookies into the cloud browser context.
        # state.json contains valid alibaba.com session cookies from ali login/keepalive.
        # These work on rfq.alibaba.com because they're scoped to .alibaba.com domain.
        from ali_cli.config import load_session
        saved = load_session()
        if saved and saved.get("cookies"):
            try:
                context.add_cookies(saved["cookies"])
                log("  Injected session cookies from state.json")
            except Exception as e:
                log(f"  [yellow]Cookie injection warning: {e}[/yellow]")

        # Reuse existing page if available, else create new
        if context.pages:
            page = context.pages[0]
        else:
            page = context.new_page()

        # Navigate to RFQ page
        log("  Navigating to rfq.alibaba.com...")
        with step("post-rfq", "navigate_to_rfq_page", page=page):
            page.goto(
                "https://rfq.alibaba.com/rfq/profession.htm",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(8000)

        # Dismiss location popup if present
        try:
            page.get_by_text("Keep current settings").click(timeout=3000)
            page.wait_for_timeout(1000)
        except Exception:
            pass

        # Check auth — My store / Buyer Central in header = logged in
        auth_ok = page.evaluate("""() => {
            const t = document.body?.innerText || '';
            return t.includes('My store') || t.includes('Buyer Central');
        }""")
        if not auth_ok:
            log("  Cookies didn't auth — falling back to OTP login...")
            try:
                _do_inline_login(page, log)
                # Seed cookies across domains after OTP login
                for seed_url in [
                    "https://www.alibaba.com/",
                    "https://i.alibaba.com/buyer/home",
                    "https://message.alibaba.com/message/messenger.htm",
                ]:
                    try:
                        page.goto(seed_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(3000)
                    except Exception:
                        pass
                # Navigate back to RFQ page
                page.goto(
                    "https://rfq.alibaba.com/rfq/profession.htm",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.wait_for_timeout(8000)
                auth_ok = page.evaluate("""() => {
                    const t = document.body?.innerText || '';
                    return t.includes('My store') || t.includes('Buyer Central');
                }""")
            except Exception as e:
                log(f"  [red]Login failed: {e}[/red]")

            if not auth_ok:
                log("  [red]Not authenticated — run 'ali login' first[/red]")
                result["error"] = "not_authenticated"
                return result

        log("  ✅ Authenticated")

        log_step("post-rfq", "auth_check", status="ok", url=page.url)

        # Step 1: Upload file if provided
        file_saved_path = None
        file_download_url = None
        if attachment:
            abs_path = str(Path(attachment).expanduser().resolve())
            if not os.path.exists(abs_path):
                page.close()
                result["error"] = f"File not found: {abs_path}"
                return result

            log(f"  Uploading {os.path.basename(abs_path)}...")
            file_input = page.query_selector('input[name="file"]')
            if file_input:
                file_input.set_input_files(abs_path)
                page.wait_for_timeout(8000)

                # Confirm upload via DOM (file item appears in upload list)
                upload_info = page.evaluate("""() => {
                    // Check multiple possible selectors for uploaded file items
                    const items = document.querySelectorAll(
                        '.new-top-banner-file-item, .ant-upload-list-item, [class*="file-item"]'
                    );
                    const names = Array.from(items).map(el => el.textContent?.trim()?.substring(0, 60));
                    return {uploaded: items.length > 0, count: items.length, names};
                }""")
                if upload_info.get("uploaded"):
                    log(f"  ✅ File uploaded: {upload_info.get('names', [])}")
                else:
                    log("  [yellow]Upload may have failed — file item not found in DOM[/yellow]")
            else:
                log("  [yellow]No file input found[/yellow]")

        # Step 2: Fill textarea
        log_step("post-rfq", "fill_subject", status="ok", details={"subject": subject[:60]})
        log(f"  Filling subject: {subject[:60]}...")
        page.evaluate("""(text) => {
            const ta = document.querySelector('textarea.ant-input');
            if (ta) {
                ta.focus();
                const set = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value'
                ).set;
                set.call(ta, text);
                ta.dispatchEvent(new Event('input', {bubbles: true}));
                ta.dispatchEvent(new Event('change', {bubbles: true}));
            }
        }""", subject)
        page.wait_for_timeout(1000)

        # Step 3: Click "Write RFQ details" and catch the popup
        log("  Opening RFQ form...")
        form_page = None
        try:
            with step("post-rfq", "open_rfq_form", page=page):
                with page.expect_popup(timeout=30000) as popup_info:
                    # Use exact text match — class selector hits social sharing buttons too
                    page.get_by_text("Write RFQ details", exact=True).click(timeout=5000)
                form_page = popup_info.value
                form_page.wait_for_load_state("domcontentloaded", timeout=30000)
                log(f"  Form opened: {form_page.url[:80]}")
        except Exception as e:
            # Check if a new page was opened in context
            for pg in context.pages:
                if 'rfqForm' in pg.url:
                    form_page = pg
                    break
            if not form_page:
                log_error("post-rfq", "open_rfq_form", e, url=page.url)
                log(f"  [red]Failed to open form: {e}[/red]")
                page.close()
                result["error"] = f"form_open_failed: {e}"
                return result

        result["url"] = form_page.url

        # Step 4: Wait for AI generation to complete
        log("  Waiting for AI generation...")
        for i in range(60):  # up to 60 seconds
            form_page.wait_for_timeout(1000)
            ready = form_page.evaluate("""() => {
                const qty = document.querySelector('#quantityInput');
                const submit = document.querySelector('button[type="submit"]');
                const content = document.body.innerText;
                return {
                    hasQtyInput: !!qty,
                    hasSubmit: submit ? submit.textContent.trim() : '',
                    hasDetail: content.includes('Detailed requirements'),
                    hasPostRequest: content.includes('Post request'),
                };
            }""")
            if ready.get('hasQtyInput') and ready.get('hasPostRequest'):
                log(f"  ✅ Form ready after {i+1}s")
                break
        else:
            log("  [yellow]Form may not be fully loaded[/yellow]")

        form_page.wait_for_timeout(3000)

        # Step 5: Wait for AI generation to complete, then extract form data.
        # Alibaba AI renders the product name and requirements as React-managed
        # DOM text — NOT as native input.value. We detect completion by checking
        # when the percentage indicator disappears AND "Apply or modify" appears
        # (which means the AI finished writing the requirements).
        log("  Waiting for AI to finish...")
        for ai_wait in range(45):
            gen_done = form_page.evaluate(r"""() => {
                const t = document.body?.innerText || '';
                const hasPercent = /\d+%/.test(t);
                const hasGenerating = t.includes('generating') || t.includes('Generating');
                const hasApplyModify = t.includes('Apply or modify');
                return !hasPercent && !hasGenerating && hasApplyModify;
            }""")
            if gen_done:
                log(f"  ✅ AI finished after {(ai_wait+1)*2}s")
                break
            time.sleep(2)
        else:
            log("  [yellow]AI generation may not have finished — proceeding anyway[/yellow]")
        form_page.wait_for_timeout(2000)

        # Extract form data. React renders the product name via innerText in the
        # input's parent, and requirements as visible text (not textarea.value).
        form_data = form_page.evaluate("""() => {
            // Product name — React-managed input. Try .value first, fall back to
            // reading the visible text after "Product name" label.
            const inputs = document.querySelectorAll('input[type="text"]');
            let productName = '';
            for (const inp of inputs) {
                if (inp.value && inp.value.length > 5 && inp.id !== 'quantityInput') {
                    productName = inp.value;
                    break;
                }
            }
            // Fallback: extract from body text between "Product name" and "Product category"
            if (!productName) {
                const t = document.body?.innerText || '';
                const parts = t.split('Product name');
                if (parts.length > 1) {
                    const afterName = parts[1];
                    const catIdx = afterName.indexOf('Product category');
                    if (catIdx > 0) {
                        productName = afterName.substring(0, catIdx).trim();
                    }
                }
            }

            // Category — button with ">>"
            let category = '';
            const catBtn = Array.from(document.querySelectorAll('button')).find(b =>
                b.textContent?.includes('>>')
            );
            if (catBtn) category = catBtn.textContent.trim();

            // Requirements — try textarea.value, then extract from body text
            const textarea = document.querySelector('textarea');
            let detail = textarea?.value || '';
            if (!detail) {
                const t = document.body?.innerText || '';
                // Split on "Discard" and take what's between it and "Generate images"
                const parts = t.split('Discard');
                if (parts.length > 1) {
                    const afterDiscard = parts[parts.length - 1];
                    const endIdx = afterDiscard.search(/Generate images|Upload a jpg|Sourcing quantity/);
                    if (endIdx > 0) {
                        detail = afterDiscard.substring(0, endIdx).trim();
                    } else {
                        detail = afterDiscard.substring(0, 500).trim();
                    }
                }
            }

            // Quantity
            const qtyInput = document.querySelector('#quantityInput');

            return {
                product_name: productName,
                category: category,
                detail: detail.substring(0, 1000),
                quantity: qtyInput?.value || '',
            };
        }""")
        result["form_data"] = form_data
        log(f"  Product: {form_data.get('product_name', '?')[:80]}")
        log(f"  Category: {form_data.get('category', '?')}")
        if form_data.get('detail'):
            log(f"  Detail: {form_data['detail'][:120]}...")

        # Step 6: Fill quantity if provided
        if quantity:
            qty_input = form_page.locator('#quantityInput')
            qty_input.fill(str(quantity))
            form_page.wait_for_timeout(500)
            log(f"  Quantity: {quantity} {unit}")

        # Step 7: Accept AI-generated description into the textarea form field.
        # Alibaba shows the AI text as a read-only preview — must click "Apply or modify"
        # to transfer it into the actual textarea that the form submits.
        log("  Accepting AI-generated description...")
        try:
            form_page.get_by_text("Apply or modify").click(timeout=5000)
            form_page.wait_for_timeout(3000)
            log("  ✅ Description applied to form")
        except Exception:
            log("  [yellow]'Apply or modify' button not found — description may already be in textarea[/yellow]")

        # Override description if custom one provided
        if description:
            log("  Setting custom description...")
            try:
                textarea = form_page.locator('textarea').first
                textarea.fill(description)
                form_page.wait_for_timeout(1000)
            except Exception:
                log("  [yellow]Could not set custom description[/yellow]")

        # Step 8: Check required checkboxes
        form_page.evaluate("""() => {
            const cbs = document.querySelectorAll('input[type="checkbox"]');
            for (const cb of cbs) {
                if (!cb.checked) cb.click();
            }
        }""")
        form_page.wait_for_timeout(500)

        # Re-read form data after filling quantity (so output has it)
        if quantity:
            form_data["quantity"] = str(quantity)
            result["form_data"] = form_data

        if dry_run:
            log("\n  [yellow]DRY RUN — stopping before submit[/yellow]")
            result["success"] = True
            result["dry_run"] = True
            form_page.close()
            page.close()
            return result

        # Step 9: Submit
        log_step("post-rfq", "submit_rfq", status="ok", url=form_page.url)
        log("\n  Submitting RFQ...")

        # Set up response capture
        submit_response = {"data": None}
        def on_resp(resp):
            url = resp.url
            if 'submit' in url.lower() or 'post' in url.lower() or 'save' in url.lower():
                try:
                    submit_response["data"] = resp.text()[:5000]
                    submit_response["status"] = resp.status
                    submit_response["url"] = url
                except Exception:
                    pass

        form_page.on("response", on_resp)

        try:
            form_page.locator('button[type="submit"]:has-text("Post request")').click(timeout=10000)
        except Exception:
            # Try alternative click methods
            form_page.evaluate("""() => {
                const btn = document.querySelector('button[type="submit"]');
                if (btn) btn.click();
            }""")

        form_page.wait_for_timeout(15000)

        # Check for success
        final_url = form_page.url
        final_content = form_page.evaluate("() => document.body.innerText.substring(0, 2000)")

        if submit_response.get("data"):
            log(f"  Submit response: {submit_response}")
            result["submit_response"] = submit_response

        # Look for success indicators
        if "success" in final_content.lower() or "posted" in final_content.lower():
            result["success"] = True
            log("  ✅ RFQ posted successfully!")
        elif final_url != result["url"]:
            result["success"] = True
            log(f"  ✅ Redirected to: {final_url}")
        else:
            # Check for error messages
            error_msg = form_page.evaluate("""() => {
                const errors = document.querySelectorAll('.error, [class*="error"], .ant-form-item-explain-error');
                return Array.from(errors).map(e => e.textContent.trim()).join('; ');
            }""")
            if error_msg:
                result["error"] = error_msg
                log(f"  [red]Error: {error_msg}[/red]")
            else:
                result["success"] = True
                log("  ✅ RFQ submitted (no explicit confirmation detected)")

        result["final_url"] = final_url

        # Extract RFQ ID from success URL
        if "rfqRequestID=" in final_url:
            import re as _re
            m = _re.search(r'rfqRequestID=(\d+)', final_url)
            if m:
                result["rfq_id"] = m.group(1)
                log(f"  RFQ ID: {result['rfq_id']}")
                log(f"  View at: https://mysourcing.alibaba.com/rfq/request/rfq_manage_list.htm")

        try:
            form_page.close()
        except Exception:
            pass
        page.close()

    return result
