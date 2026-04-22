"""Playwright browser management for Alibaba — local headless + cloud CDP.

All data access goes through page.evaluate() to leverage the browser's
cookie/CSRF/fingerprint context. Direct HTTP calls to Alibaba return 503.
"""

import base64
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from ali_cli.config import load_session, save_session, save_cookies, clear_session
from ali_cli.errors import step as log_step_ctx, log_error, AliError


class SessionExpiredError(RuntimeError):
    """Raised when the Alibaba session has expired and re-login is needed."""
    pass


MESSENGER_URL = "https://message.alibaba.com/message/messenger.htm"
BUYING_LEADS_URL = "https://message.alibaba.com/message/buyingLeads.htm?activeTab=rfq&onlyRfq=true"
MYSOURCING_RFQ_URL = "https://mysourcing.alibaba.com/rfq/request/rfq_manage_list.htm"


class BrowserManager:
    """Manages a Playwright browser with session persistence."""

    def __init__(self, headless=True, timeout=30000, cdp_url=None):
        self.headless = headless
        self.timeout = timeout
        self.cdp_url = cdp_url
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.close()

    def start(self):
        self._playwright = sync_playwright().start()

        if self.cdp_url:
            self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
            self._context = self._browser.contexts[0]
        else:
            self._browser = self._playwright.chromium.launch(headless=self.headless)
            viewport = {"width": 1440, "height": 1080}
            session = load_session()
            if session:
                state = {k: v for k, v in session.items() if not k.startswith("_")}
                try:
                    self._context = self._browser.new_context(
                        storage_state=state, viewport=viewport
                    )
                except Exception:
                    self._context = self._browser.new_context(viewport=viewport)
            else:
                self._context = self._browser.new_context(viewport=viewport)

        self._context.set_default_timeout(self.timeout)
        self._page = self._context.new_page()

    def close(self):
        """Gracefully shut down browser resources. Safe to call multiple times."""
        for attr, method in [("_page", "close"), ("_context", "close"), ("_browser", "close")]:
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            # Don't close context/browser for CDP connections (shared)
            if self.cdp_url and attr in ("_context", "_browser"):
                continue
            try:
                getattr(obj, method)()
            except Exception:
                pass
            setattr(self, attr, None)
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    @property
    def page(self):
        return self._page

    # ── Session helpers ──────────────────────────────────────────────

    def is_logged_in(self):
        return "login" not in self._page.url.lower()

    def ensure_logged_in(self):
        try:
            self._page.goto(
                "https://www.alibaba.com/",
                wait_until="domcontentloaded",
                timeout=20000,
            )
        except PlaywrightTimeout:
            raise RuntimeError("Timeout checking login status.")
        self._page.wait_for_timeout(2000)
        if not self.is_logged_in():
            clear_session()
            raise SessionExpiredError("Session expired. Run 'ali login' to re-authenticate.")

    def save_current_session(self):
        """Save browser state. Falls back to cookies() if storage_state() fails."""
        try:
            state = self._context.storage_state()
            save_session(state)
        except Exception:
            # context destroyed or navigation race — use cookies() which is safer
            pass
        try:
            cookies = self._context.cookies()
            save_cookies(cookies)
        except Exception:
            pass

    def _get_ctoken(self):
        return self._page.evaluate(
            """() => {
            const ct = document.cookie.split(';')
                .map(c => c.trim())
                .find(c => c.startsWith('_tb_token_='));
            return ct ? ct.split('=')[1] : '';
        }"""
        )

    def _get_csrf_token(self):
        """Get CSRF token from onetalk for POST requests."""
        return self._page.evaluate(
            """async () => {
            const resp = await fetch('//onetalk.alibaba.com/csrf/getToken.htm', {
                method: 'POST',
                credentials: 'include'
            });
            const data = await resp.json();
            return data.token || '';
        }"""
        )

    # ── Navigation ────────────────────────────────────────────────────

    def _ensure_on_messenger(self):
        if "message.alibaba.com/message/messenger" not in self._page.url:
            try:
                self._page.goto(
                    MESSENGER_URL,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                self._page.wait_for_timeout(4000)
            except PlaywrightTimeout:
                raise RuntimeError("Timeout navigating to messenger. Check network/session.")
        if "login" in self._page.url.lower():
            raise SessionExpiredError("Session expired. Run 'ali login'.")

    def _ensure_on_buying_leads(self):
        if "buyingLeads" not in self._page.url:
            try:
                self._page.goto(
                    BUYING_LEADS_URL,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                self._page.wait_for_timeout(4000)
            except PlaywrightTimeout:
                raise RuntimeError("Timeout navigating to buying leads.")
        if "login" in self._page.url.lower():
            raise SessionExpiredError("Session expired. Run 'ali login'.")

    # ── Messenger API calls ──────────────────────────────────────────

    def get_unread_summary(self):
        """Get unread message summary with conversation list from onetalk API.

        Returns the full API response dict with code, data.hasLogin,
        data.unreadCount, and data.list (conversations with unread messages).
        """
        self._ensure_on_messenger()
        ctoken = self._get_ctoken()
        return self._page.evaluate(
            """async (ctoken) => {
                const resp = await fetch(
                    '//onetalk.alibaba.com/message/manager/unread.htm?ctoken=' + ctoken,
                    {credentials: 'include'}
                );
                return await resp.json();
            }""",
            ctoken,
        )

    def get_conversations(self, limit=50):
        """Get full conversation list from messenger page in-memory data.

        This accesses window.__conversationListFullData__ which contains
        all 1000+ conversations loaded by the messenger SPA.
        Falls back to DOM scraping of contact list if SPA data not populated.
        """
        self._ensure_on_messenger()
        self._page.wait_for_timeout(2000)
        result = self._page.evaluate(
            """(limit) => {
                const data = window.__conversationListFullData__ || [];
                const filtered = data.filter(c => c.visible !== false).slice(0, limit);
                if (filtered.length > 0) {
                    return filtered.map(c => ({
                        name: c.name || '',
                        companyName: c.companyName || '',
                        unreadCount: c.unreadCount || 0,
                        cid: c.cid || '',
                        lastContactTime: c.lastContactTime || 0,
                        loginId: c.loginId || '',
                        accountId: c.accountId || 0,
                    }));
                }

                // Fallback: scrape contact items from the DOM
                const items = document.querySelectorAll('.contact-item-container');
                if (items.length === 0) return [];
                const results = [];
                for (let i = 0; i < Math.min(items.length, limit); i++) {
                    const el = items[i];
                    const nameEl = el.querySelector('.name, [class*="name"]');
                    const companyEl = el.querySelector('.company, [class*="company"]');
                    const previewEl = el.querySelector('.msg-preview, [class*="preview"]');
                    const unreadEl = el.querySelector('.unread-count, [class*="unread"]');
                    results.push({
                        name: nameEl ? nameEl.textContent.trim() : '',
                        companyName: companyEl ? companyEl.textContent.trim() : '',
                        unreadCount: unreadEl ? parseInt(unreadEl.textContent.trim()) || 0 : 0,
                        cid: '',
                        lastContactTime: 0,
                        loginId: '',
                        accountId: 0,
                        _source: 'dom_scrape',
                    });
                }
                return results;
            }""",
            limit,
        )
        return result if result else []

    def get_messages(self, conversation_id, count=20, before_ts=None):
        """Get message history for a conversation via listRecentMessage API.

        Args:
            conversation_id: e.g. "<buyer_id>-<seller_id>#11011@icbu"
            count: Number of messages to fetch
            before_ts: Timestamp for pagination (fetch messages before this time)

        Returns full API response with data.messageList and data.hasMore.
        """
        self._ensure_on_messenger()
        csrf = self._get_csrf_token()
        return self._page.evaluate(
            """async ({cid, count, beforeTs, csrf}) => {
                const params = {
                    pointTimeStamp: beforeTs || null,
                    forward: false,
                    count: count,
                    conversationId: cid
                };
                const body = 'params=' + encodeURIComponent(JSON.stringify(params)) + '&_csrf=' + csrf;
                const resp = await fetch('//onetalk.alibaba.com/message/listRecentMessage.htm', {
                    method: 'POST',
                    credentials: 'include',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                    body: body
                });
                return await resp.json();
            }""",
            {"cid": conversation_id, "count": count, "beforeTs": before_ts, "csrf": csrf},
        )

    def send_message(self, conversation_index, text):
        """Send a message by clicking a conversation and using the textarea.

        The PaaS WebSocket SDK handles actual delivery — we interact via DOM.
        """
        self._ensure_on_messenger()
        self._page.wait_for_timeout(1000)

        # Click conversation
        click_result = self._page.evaluate(
            """(idx) => {
                const items = document.querySelectorAll('.contact-item-container');
                if (idx >= items.length) return {error: 'Index ' + idx + ' out of range (max ' + items.length + ')'};
                items[idx].click();
                const nameEl = items[idx].querySelector('.name, [class*="name"]');
                return {ok: true, name: nameEl ? nameEl.textContent.trim() : ''};
            }""",
            conversation_index,
        )
        if isinstance(click_result, dict) and click_result.get("error"):
            raise RuntimeError(click_result["error"])

        self._page.wait_for_timeout(2000)

        # Type message and send
        send_result = self._page.evaluate(
            """(text) => {
                const textarea = document.querySelector('.send-textarea');
                if (!textarea) return {error: 'No message input found'};

                // Use native setter to trigger React state
                const nativeSet = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value'
                ).set;
                nativeSet.call(textarea, text);
                textarea.dispatchEvent(new Event('input', {bubbles: true}));
                textarea.dispatchEvent(new Event('change', {bubbles: true}));

                return new Promise(resolve => {
                    setTimeout(() => {
                        const sendBtn = document.querySelector('.send-btn-bar');
                        if (sendBtn) {
                            sendBtn.click();
                            resolve({ok: true});
                        } else {
                            textarea.dispatchEvent(new KeyboardEvent('keydown', {
                                key: 'Enter', keyCode: 13, bubbles: true
                            }));
                            resolve({ok: true, method: 'enter'});
                        }
                    }, 300);
                });
            }""",
            text,
        )
        if isinstance(send_result, dict) and send_result.get("error"):
            raise RuntimeError(send_result["error"])

        return click_result.get("name", "")

    def extract_cid_by_clicking(self, name: str) -> str:
        """Click a conversation by name in the DOM and extract its CID.

        After clicking, the SPA updates internal state with the conversation ID.
        We extract it from the URL hash or window state.
        """
        self._ensure_on_messenger()
        result = self._page.evaluate(
            """(name) => {
                const items = document.querySelectorAll('.contact-item-container');
                const nameLower = name.toLowerCase();
                for (const item of items) {
                    const nameEl = item.querySelector('.name, [class*="name"]');
                    if (nameEl && nameEl.textContent.trim().toLowerCase().includes(nameLower)) {
                        item.click();
                        return {clicked: true, text: nameEl.textContent.trim()};
                    }
                }
                return {clicked: false};
            }""",
            name,
        )

        if not result or not result.get("clicked"):
            return ""

        self._page.wait_for_timeout(2000)

        # Try to extract CID from page state after click
        cid = self._page.evaluate(
            """() => {
                // Check URL hash
                const hash = window.location.hash;
                const cidMatch = hash.match(/cid=([^&]+)/);
                if (cidMatch) return cidMatch[1];

                // Check for active conversation in React state
                const active = window.__activeConversation__;
                if (active && active.cid) return active.cid;

                // Check URL for conversationId param
                const url = new URL(window.location.href);
                const convId = url.searchParams.get('conversationId') || url.searchParams.get('cid');
                if (convId) return convId;

                // Try to find it in the conversation list data that may have loaded
                const data = window.__conversationListFullData__ || [];
                const clicked = document.querySelector('.contact-item-container.active, .contact-item-container.selected, [class*="contact-item"][class*="active"]');
                if (clicked) {
                    const clickedName = clicked.querySelector('.name, [class*="name"]');
                    if (clickedName) {
                        const n = clickedName.textContent.trim().toLowerCase();
                        for (const c of data) {
                            if ((c.name || '').toLowerCase() === n && c.cid) return c.cid;
                        }
                    }
                }

                return '';
            }"""
        )
        return cid or ""

    # ── File download/upload ────────────────────────────────────────

    def download_file(self, url: str, output_path: str) -> bool:
        """Download a file using browser cookies (clouddisk.alibaba.com needs auth).

        Opens the URL in a new page (same browser context = shared cookies),
        intercepts the response, and saves to disk. This avoids CORS issues
        that block fetch() from message.alibaba.com to clouddisk.alibaba.com.
        Returns True on success, raises on failure.
        """
        import base64 as b64_mod

        # Try new-page approach first (avoids CORS entirely)
        new_page = self._context.new_page()
        try:
            # Set up response capture
            captured = {"data": None, "content_type": None}

            def handle_response(response):
                try:
                    if response.url.startswith(url[:60]) or "clouddisk" in response.url:
                        if response.status == 200:
                            captured["data"] = response.body()
                            captured["content_type"] = response.headers.get("content-type", "")
                except Exception:
                    pass

            new_page.on("response", handle_response)
            new_page.goto(url, wait_until="load", timeout=30000)
            new_page.wait_for_timeout(2000)

            # If response capture worked, use it
            if captured["data"] and len(captured["data"]) > 100:
                os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(captured["data"])
                return True

            # Fallback: try to grab image from the rendered page
            result = new_page.evaluate("""() => {
                const img = document.querySelector('img');
                if (img && img.src) return {type: 'redirect', src: img.src};
                // Check if page body has raw content (e.g., image displayed directly)
                return {type: 'none'};
            }""")

            if result and result.get("type") == "redirect" and result.get("src"):
                # Page rendered an img tag — fetch it from this page's context
                img_data = new_page.evaluate("""async (src) => {
                    try {
                        const resp = await fetch(src, {credentials: 'include'});
                        if (!resp.ok) return {error: 'HTTP ' + resp.status};
                        const buf = await resp.arrayBuffer();
                        const bytes = new Uint8Array(buf);
                        let binary = '';
                        const chunkSize = 8192;
                        for (let i = 0; i < bytes.length; i += chunkSize) {
                            const chunk = bytes.subarray(i, Math.min(i + chunkSize, bytes.length));
                            binary += String.fromCharCode.apply(null, chunk);
                        }
                        return {ok: true, data: btoa(binary), size: bytes.length};
                    } catch(e) {
                        return {error: e.message};
                    }
                }""", result["src"])
                if img_data and img_data.get("ok"):
                    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                    file_data = b64_mod.b64decode(img_data["data"])
                    with open(output_path, "wb") as f:
                        f.write(file_data)
                    return True

            raise RuntimeError("Could not capture file content from clouddisk URL")

        finally:
            try:
                new_page.close()
            except Exception:
                pass

        # Should not reach here — new_page approach handles all paths
        raise RuntimeError("Download failed: no data captured")

    def send_file(self, conversation_index: int, file_path: str, caption: str = "") -> str:
        """Send a file/image to a conversation via the hidden file input.

        Uses Playwright's set_input_files() on the hidden <input type="file">.
        Alibaba's React upload handler triggers automatically.
        Returns the contact name.
        """
        self._ensure_on_messenger()
        self._page.wait_for_timeout(1000)

        # Click conversation first
        click_result = self._page.evaluate(
            """(idx) => {
                const items = document.querySelectorAll('.contact-item-container');
                if (idx >= items.length) return {error: 'Index ' + idx + ' out of range (max ' + items.length + ')'};
                items[idx].click();
                const nameEl = items[idx].querySelector('.name, [class*="name"]');
                return {ok: true, name: nameEl ? nameEl.textContent.trim() : ''};
            }""",
            conversation_index,
        )
        if isinstance(click_result, dict) and click_result.get("error"):
            raise RuntimeError(click_result["error"])

        self._page.wait_for_timeout(2000)

        # Find the hidden file input and upload
        file_input = self._page.query_selector('input[type="file"]')
        if not file_input:
            raise RuntimeError("No file input found in messenger. The upload widget may not be loaded.")

        abs_path = str(Path(file_path).resolve())
        if not os.path.exists(abs_path):
            raise RuntimeError(f"File not found: {abs_path}")

        file_input.set_input_files(abs_path)
        # Wait for upload to process
        self._page.wait_for_timeout(5000)

        # If caption provided, type and send it
        if caption:
            self._page.evaluate(
                """(text) => {
                    const textarea = document.querySelector('.send-textarea');
                    if (!textarea) return;
                    const nativeSet = Object.getOwnPropertyDescriptor(
                        window.HTMLTextAreaElement.prototype, 'value'
                    ).set;
                    nativeSet.call(textarea, text);
                    textarea.dispatchEvent(new Event('input', {bubbles: true}));
                    textarea.dispatchEvent(new Event('change', {bubbles: true}));
                    setTimeout(() => {
                        const sendBtn = document.querySelector('.send-btn-bar');
                        if (sendBtn) sendBtn.click();
                    }, 300);
                }""",
                caption,
            )
            self._page.wait_for_timeout(2000)

        return click_result.get("name", "")

    def get_rfq_quote_details(self, rfq_id: int) -> list:
        """Scrape quote pricing from the RFQ comparison page.

        Navigates to the buying leads page filtered by rfqId, waits for the
        comparison table to render, then extracts company names and prices.

        Returns list of dicts: [{company, price, unit, product}]
        """
        url = f"https://message.alibaba.com/message/buyingLeads.htm?hashParams=%7B%22rfqId%22%3A{rfq_id}%7D#/"
        self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._page.wait_for_timeout(8000)  # SPA needs time to render comparison

        result = self._page.evaluate(r"""() => {
            const text = document.body.innerText;
            const compIdx = text.indexOf('quotations comparison');
            if (compIdx === -1) return [];

            const section = text.substring(compIdx);

            // Extract company names (between "comparison" and "Vendor comparison")
            const vendorIdx = section.indexOf('Vendor comparison');
            const headerSection = vendorIdx > 0 ? section.substring(0, vendorIdx) : section.substring(0, 1000);

            // Companies appear before "Chat now" entries
            const companies = [];
            const companyRegex = /([A-Z][^\n]{5,80}(?:Co\.,?\s*Ltd\.?|Limited|Inc\.|Corp\.|Technology))/g;
            let match;
            while ((match = companyRegex.exec(headerSection)) !== null) {
                const name = match[1].trim();
                if (!name.includes('Chat now') && !name.includes('Add notes')) {
                    companies.push(name);
                }
            }

            // Extract prices
            const priceRegex = /USD\s+([\d.]+)\/(Piece|Pieces|piece)/gi;
            const prices = [];
            while ((match = priceRegex.exec(section)) !== null) {
                prices.push({price: parseFloat(match[1]), unit: match[2]});
            }

            // Extract product descriptions (between "Product" and "Unit price")
            const prodIdx = section.indexOf('\nProduct\n');
            const priceIdx = section.indexOf('\nUnit price\n');
            let products = [];
            if (prodIdx > 0 && priceIdx > prodIdx) {
                const prodSection = section.substring(prodIdx + 9, priceIdx);
                products = prodSection.split('View product details').map(p => p.trim()).filter(p => p.length > 5);
            }

            // Combine
            const quotes = [];
            for (let i = 0; i < Math.max(companies.length, prices.length); i++) {
                quotes.push({
                    company: companies[i] || 'Unknown',
                    price: prices[i] ? prices[i].price : null,
                    unit: prices[i] ? prices[i].unit : 'Piece',
                    product: products[i] || ''
                });
            }

            return quotes;
        }""")

        return result if isinstance(result, list) else []

    def _ensure_on_mysourcing(self):
        """Navigate to mysourcing.alibaba.com for same-origin JSONP calls.

        JSONP to mysourcing.alibaba.com only works from the same origin.
        Calling from message.alibaba.com results in cross-origin failures.
        """
        if "mysourcing.alibaba.com" not in self._page.url:
            try:
                self._page.goto(
                    MYSOURCING_RFQ_URL,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                self._page.wait_for_timeout(3000)
            except PlaywrightTimeout:
                raise RuntimeError("Timeout navigating to mysourcing.")
        if "login" in self._page.url.lower():
            raise SessionExpiredError("Session expired. Run 'ali login'.")

    # ── RFQ API calls ────────────────────────────────────────────────

    def get_rfq_list(self, page_num=1, page_size=20):
        """Get RFQ list via JSONP from mysourcing.alibaba.com.

        Navigates to mysourcing.alibaba.com first (JSONP is same-origin only —
        cross-origin calls from message.alibaba.com fail silently).
        Falls back to DOM scraping if JSONP fails.

        Returns the full API response with data.list and data.total.
        """
        self._ensure_on_mysourcing()
        self._page.wait_for_timeout(2000)

        result = self._page.evaluate(
            """async ({pageNum, pageSize}) => {
                // Try IcbuIM library first (available on mysourcing pages)
                let tries = 0;
                while ((!window.IcbuIM || !window.IcbuIM.lib) && tries < 20) {
                    await new Promise(r => setTimeout(r, 500));
                    tries++;
                }

                if (window.IcbuIM && window.IcbuIM.lib) {
                    try {
                        return await window.IcbuIM.lib.requestHelper.jsonp(
                            '//mysourcing.alibaba.com/rfq/request/my_rfq_list_ajax.do',
                            {page: pageNum, pageSize: pageSize, orderBy: 'gmt_create', orderDirection: 'DESC'}
                        );
                    } catch(e) { /* fall through to script-tag JSONP */ }
                }

                // Fallback: JSONP via script tag (same-origin from mysourcing)
                return await new Promise((resolve) => {
                    const cb = 'jsonp_' + Date.now();
                    window[cb] = function(data) {
                        delete window[cb];
                        try { document.body.removeChild(script); } catch(e) {}
                        resolve(data);
                    };
                    const script = document.createElement('script');
                    const params = new URLSearchParams({
                        callback: cb,
                        page: String(pageNum),
                        pageSize: String(pageSize),
                        orderBy: 'gmt_create',
                        orderDirection: 'DESC'
                    });
                    script.src = '//mysourcing.alibaba.com/rfq/request/my_rfq_list_ajax.do?' + params;
                    script.onerror = () => resolve({code: 500, message: 'JSONP failed'});
                    document.body.appendChild(script);
                    setTimeout(() => resolve({code: 504, message: 'JSONP timeout'}), 15000);
                });
            }""",
            {"pageNum": page_num, "pageSize": page_size},
        )

        # If JSONP failed, try DOM scraping as last resort
        if isinstance(result, dict) and result.get("code") in (500, 504):
            dom_result = self._scrape_rfq_list_dom()
            if dom_result:
                return dom_result

        return result

    def _scrape_rfq_list_dom(self):
        """DOM-scraping fallback for RFQ list.

        Parses visible RFQ cards on the mysourcing page. Card structure:
        - Date/ID line
        - Subject line
        - Quantity line
        - Quotes count
        - Status

        Returns a dict matching the JSONP response format, or None if parsing fails.
        """
        self._ensure_on_mysourcing()
        self._page.wait_for_timeout(3000)

        return self._page.evaluate(
            """() => {
                // Try multiple known container selectors
                const selectors = [
                    '.rfq-list-container .rfq-item',
                    '.rfq-list .rfq-card',
                    '[class*="rfq-list"] [class*="rfq-item"]',
                    'table.rfq-table tbody tr',
                    '.next-table-body tr'
                ];

                let items = [];
                for (const sel of selectors) {
                    items = document.querySelectorAll(sel);
                    if (items.length > 0) break;
                }

                if (items.length === 0) return null;

                const rfqs = [];
                for (const item of items) {
                    const text = item.textContent || '';
                    // Extract RFQ ID (numeric pattern near date)
                    const idMatch = text.match(/\\b(\\d{10,})\\b/);
                    // Extract date (YYYY-MM-DD)
                    const dateMatch = text.match(/(\\d{4}-\\d{2}-\\d{2})/);
                    // Extract quotes count
                    const quotesMatch = text.match(/(\\d+)\\s*(?:quote|quotation)/i);
                    // Extract status
                    const statusMatch = text.match(/\\b(Approved|Expired|Closed|Open|Pending)\\b/i);

                    rfqs.push({
                        id: idMatch ? parseInt(idMatch[1]) : 0,
                        gmtCreate: dateMatch ? dateMatch[1] : '',
                        subject: text.substring(0, 200).replace(/\\s+/g, ' ').trim(),
                        quotesReceived: quotesMatch ? parseInt(quotesMatch[1]) : 0,
                        status: statusMatch ? statusMatch[1] : 'Unknown',
                        _source: 'dom_scrape'
                    });
                }

                return {
                    code: 200,
                    data: {
                        total: rfqs.length,
                        list: rfqs
                    },
                    _source: 'dom_scrape'
                };
            }"""
        )
