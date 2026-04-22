"""Ali monitor — full packaging check in a single browser session.

One browser launch handles everything:
  1. Unread message summary
  2. Read each unread conversation
  3. RFQ list with unread quote counts
  4. Quote details for active RFQs

~30s total vs 200s+ for separate ali commands.
"""

import time
from ali_cli.models import Message, RFQ
from ali_cli.errors import start_run, step, log_step


def run_monitor(console=None) -> dict:
    """Run the full packaging monitor in a single browser session.

    Returns structured dict:
      logged_in, session_age_hours, unread_conversations, rfqs, elapsed_seconds, errors
    """
    from ali_cli.browser import BrowserManager, SessionExpiredError
    from ali_cli.session_manager import state_age_hours

    run_id = start_run("monitor")
    start = time.time()
    result = {
        "logged_in": False,
        "session_age_hours": None,
        "unread_conversations": [],
        "rfqs": {"total": 0, "unread_quotes": 0, "active": []},
        "elapsed_seconds": 0,
        "errors": [],
        "run_id": run_id,
    }

    age = state_age_hours()
    result["session_age_hours"] = round(age, 1) if age is not None else None

    def log(msg):
        if console:
            console.print(msg)

    try:
        with BrowserManager(headless=True, timeout=30000) as bm:

            # ── Step 1: Unread summary ────────────────────────────────
            with step("monitor", "ensure_on_messenger", page=bm.page):
                bm._ensure_on_messenger()

            with step("monitor", "get_unread_summary", page=bm.page, required=False):
                summary_data = bm.get_unread_summary()

                if summary_data and summary_data.get("code") == "200":
                    d = summary_data.get("data", {})
                    result["logged_in"] = d.get("hasLogin", False)
                    unread_items = d.get("list", [])
                    log(f"  Logged in: {result['logged_in']}, unread convos: {len(unread_items)}")
                else:
                    result["errors"].append("Failed to get unread summary")
                    unread_items = []

            # ── Step 2: Read each unread conversation ─────────────────
            for item in unread_items:
                cid = item.get("cid", "")
                if cid and "@" not in cid:
                    cid += "@icbu"

                conv_result = {
                    "name": item.get("name", ""),
                    "company": item.get("companyName", ""),
                    "unread": item.get("unreadCount", 0),
                    "messages": [],
                    "has_images": False,
                    "image_urls": [],
                }

                if cid:
                    with step("monitor", f"read_conversation:{conv_result['name'][:30]}",
                              page=bm.page, required=False, on_error="continue"):
                        raw = bm.get_messages(cid, count=20)
                        if raw and raw.get("code") == "200":
                            for m in raw.get("data", {}).get("messageList", []):
                                msg = Message.from_api(m)
                                conv_result["messages"].append({
                                    "text": msg.text,
                                    "is_self": msg.is_self,
                                    "time": msg.time,
                                    "type": msg.msg_type,
                                    "image_url": msg.image_url,
                                    "file_url": msg.file_url,
                                    "file_name": msg.file_name,
                                })
                                if msg.image_url:
                                    conv_result["has_images"] = True
                                    conv_result["image_urls"].append(msg.image_url)

                result["unread_conversations"].append(conv_result)
                log(f"  {conv_result['name']}: {conv_result['unread']} unread, "
                    f"{len(conv_result['messages'])} msgs"
                    f"{' 🖼' if conv_result['has_images'] else ''}")

            # ── Step 3 + 4: RFQs in same session ─────────────────────
            with step("monitor", "get_rfq_list", page=bm.page, required=False, on_error="continue"):
                rfq_data = bm.get_rfq_list(page_num=1, page_size=50)
                code = rfq_data.get("code", 0) if rfq_data else 0
                if isinstance(code, str):
                    code = int(code) if code.isdigit() else 0

                if code == 200:
                    inner = rfq_data.get("data", {})
                    result["rfqs"]["total"] = inner.get("total", 0)
                    result["rfqs"]["unread_quotes"] = inner.get("unReadQuotations", 0)
                    log(f"  RFQs: {result['rfqs']['total']} total, "
                        f"{result['rfqs']['unread_quotes']} unread quotes")

                    # Build lookup: supplier name → conversation ID (from unread convos)
                    conv_by_name = {}
                    for c in result["unread_conversations"]:
                        n = (c.get("name") or "").lower().strip()
                        if n:
                            conv_by_name[n] = c

                    for rfq_raw in inner.get("list", []):
                        rfq = RFQ.from_api(rfq_raw)
                        if rfq.quotes_received > 0 or rfq.unread_quotes > 0:
                            quotes_with_messages = []
                            for q in rfq.quotes:
                                supplier_name = f"{q.first_name} {q.last_name}".strip()
                                quote_entry = {
                                    "company": q.company_name,
                                    "name": supplier_name,
                                    "quote_id": q.quote_id,
                                    "modified": q.modified,
                                    "read": q.read,
                                    "messages": [],
                                }
                                conv = conv_by_name.get(supplier_name.lower())
                                if conv and conv.get("messages"):
                                    quote_entry["messages"] = conv["messages"]
                                quotes_with_messages.append(quote_entry)

                            # For RFQs with unread quotes, try to get pricing
                            if rfq.unread_quotes > 0:
                                with step("monitor", f"get_rfq_quote_details:{rfq.id}",
                                          page=bm.page, required=False, on_error="continue"):
                                    quote_details = bm.get_rfq_quote_details(rfq.id)
                                    if quote_details:
                                        for qd in quote_details:
                                            for qm in quotes_with_messages:
                                                if (qd['company'].lower() in qm.get('company', '').lower() or
                                                        qm.get('company', '').lower() in qd['company'].lower()):
                                                    qm['price'] = qd.get('price')
                                                    qm['price_unit'] = qd.get('unit', 'Piece')
                                                    qm['product_quoted'] = qd.get('product', '')
                                                    break

                            result["rfqs"]["active"].append({
                                "id": rfq.id,
                                "subject": rfq.subject,
                                "status": rfq.status,
                                "quotes_received": rfq.quotes_received,
                                "unread_quotes": rfq.unread_quotes,
                                "quotes": quotes_with_messages,
                            })
                else:
                    result["errors"].append(f"RFQ list code: {code}")

            with step("monitor", "save_session", page=bm.page, required=False, on_error="continue"):
                bm.save_current_session()

    except SessionExpiredError:
        result["errors"].append("Session expired — run 'ali login'")
    except Exception as e:
        result["errors"].append(f"Browser error: {e}")

    result["elapsed_seconds"] = round(time.time() - start, 1)
    log_step("monitor", "run_complete", status="ok",
             details={"elapsed": result["elapsed_seconds"], "errors": len(result["errors"])})
    return result
