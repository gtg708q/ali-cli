"""RFQ (Request for Quotation) operations — uses JSONP API via BrowserManager.

The RFQ list API is on mysourcing.alibaba.com and requires JSONP access
from the buying leads page context where IcbuIM.lib is available.
"""

from ali_cli.models import RFQ, QuoteSeller
from ali_cli.errors import step


def get_rfq_list(browser, page_num=1, page_size=20):
    """Get paginated RFQ list with quote details.

    Returns (list[RFQ], total_count, unread_quotations).
    """
    with step("rfq", "get_rfq_list", page=browser.page):
        data = browser.get_rfq_list(page_num=page_num, page_size=page_size)

        if not data:
            return [], 0, 0

        code = data.get("code", 0)
        if isinstance(code, str):
            code = int(code) if code.isdigit() else 0
        if code != 200:
            return [], 0, 0

        inner = data.get("data", {})
        total = inner.get("total", 0)
        unread = inner.get("unReadQuotations", 0)
        raw_list = inner.get("list", [])

        rfqs = [RFQ.from_api(item) for item in raw_list]
        return rfqs, total, unread


def get_rfq_quote_details(browser, rfq_id):
    """Get pricing details for quotes on a specific RFQ.
    Returns list of {company, price, unit, product}.
    """
    with step("rfq", f"get_rfq_quote_details:{rfq_id}", page=browser.page):
        return browser.get_rfq_quote_details(int(rfq_id))


def get_rfq_by_id(browser, rfq_id, page_size=50):
    """Find a specific RFQ by ID.

    Searches through the RFQ list to find the matching RFQ.
    Returns RFQ or None.
    """
    rfq_id = int(rfq_id)
    # Search through pages
    for page_num in range(1, 20):
        rfqs, total, _ = get_rfq_list(browser, page_num=page_num, page_size=page_size)
        for rfq in rfqs:
            if rfq.id == rfq_id:
                return rfq
        if page_num * page_size >= total:
            break
    return None
