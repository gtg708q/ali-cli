"""Messenger operations — high-level functions that use BrowserManager.

All API calls go through page.evaluate() in browser context.
Direct HTTP calls to Alibaba return 503.
"""

from difflib import SequenceMatcher
from typing import Optional, Tuple

from ali_cli.models import Conversation, Message, UnreadSummary
from ali_cli.errors import step


def get_unread_summary(browser) -> UnreadSummary:
    """Get unread message summary with enriched conversation list.

    Returns an UnreadSummary with conversations that have unread messages.
    """
    with step("messenger", "get_unread_summary", page=browser.page):
        data = browser.get_unread_summary()
        if not data or data.get("code") != "200":
            return UnreadSummary()
        return UnreadSummary.from_api(data.get("data", {}))


def get_conversations(browser, limit=50, unread_only=False) -> list[Conversation]:
    """Get conversation list.

    If unread_only=True, uses the unread API (returns only unread conversations).
    Otherwise uses the full in-page conversation list, falling back to unread API.
    """
    if unread_only:
        summary = get_unread_summary(browser)
        return summary.conversations

    with step("messenger", "get_conversations", page=browser.page):
        raw = browser.get_conversations(limit=limit)
        if not raw:
            # Fallback to unread API (at least returns conversations with unread)
            summary = get_unread_summary(browser)
            return summary.conversations

        return [Conversation.from_full_list(item) for item in raw]


def get_conversation_by_index(browser, index, limit=50) -> Tuple[Conversation, str]:
    """Get a specific conversation by its index in the list.

    If the conversation from the full list has no CID (DOM fallback),
    tries to find a matching conversation in the unread API by name.
    Returns (Conversation, conversation_id_for_api) or raises IndexError.
    """
    convos = get_conversations(browser, limit=limit)
    if index >= len(convos):
        raise IndexError(f"Conversation #{index + 1} not found (only {len(convos)} available)")
    conv = convos[index]

    cid = _normalize_cid(conv.cid)

    # If no CID from full list, try to resolve via unread API or click-to-extract
    if not cid and conv.name:
        cid = _resolve_cid_by_name(browser, conv.name)

    return conv, cid


def get_conversation_by_name(browser, name: str, limit=200) -> Tuple[Conversation, str]:
    """Find a conversation by fuzzy-matching supplier name.

    Searches both the full conversation list and unread conversations.
    Returns (Conversation, conversation_id_for_api) or raises ValueError.
    """
    with step("messenger", "get_conversation_by_name", page=browser.page):
        convos = get_conversations(browser, limit=limit)

        # Also search unread conversations (may have contacts not in full list)
        unread_convos = get_conversations(browser, unread_only=True)

        # Merge, dedup by cid
        seen_cids = set()
        all_convos = []
        for c in convos + unread_convos:
            key = c.cid or c.name
            if key not in seen_cids:
                seen_cids.add(key)
                all_convos.append(c)

        if not all_convos:
            raise ValueError("No conversations found.")

        # Score each conversation name against query
        name_lower = name.lower()
        scored = []
        for c in all_convos:
            c_name = (c.name or "").lower()
            c_company = (c.company_name or "").lower()

            # Exact substring match is highest priority
            if name_lower in c_name:
                score = 1.0
            elif name_lower in c_company:
                score = 0.9
            else:
                # Fuzzy match
                score = max(
                    SequenceMatcher(None, name_lower, c_name).ratio(),
                    SequenceMatcher(None, name_lower, c_company).ratio() * 0.9,
                )
            scored.append((score, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_conv = scored[0]

        if best_score < 0.3:
            top_names = [f"  {c.name} ({c.company_name})" for _, c in scored[:5]]
            raise ValueError(
                f"No conversation matching '{name}'. Top matches:\n" + "\n".join(top_names)
            )

        cid = _normalize_cid(best_conv.cid)

        # If best match has no CID, try to resolve it
        if not cid and best_conv.name:
            cid = _resolve_cid_by_name(browser, best_conv.name)

        return best_conv, cid


def _resolve_cid_by_name(browser, name: str) -> str:
    """Try to resolve a CID for a conversation that has no CID.

    Strategy:
    1. Check unread API conversations (they have CIDs)
    2. Click the conversation in the DOM and extract CID from the URL/state
    """
    # Try unread API first
    summary = get_unread_summary(browser)
    name_lower = name.lower()
    for c in summary.conversations:
        if c.cid and name_lower in (c.name or "").lower():
            return _normalize_cid(c.cid)

    # Try clicking the conversation and extracting CID from the page state
    cid = browser.extract_cid_by_clicking(name)
    if cid:
        return _normalize_cid(cid)

    return ""


def _normalize_cid(cid: str) -> str:
    """Ensure CID has @icbu suffix for API calls."""
    if cid and "@" not in cid:
        return cid + "@icbu"
    return cid


def get_messages(browser, conversation_id: str, count: int = 20, before_ts=None) -> Tuple[list[Message], bool]:
    """Get message history for a conversation.

    Returns (list of Message objects newest first, has_more).
    """
    with step("messenger", "get_messages", page=browser.page):
        data = browser.get_messages(conversation_id, count=count, before_ts=before_ts)
        if not data or data.get("code") != "200":
            return [], False

        msg_list = data.get("data", {}).get("messageList", [])
        has_more = data.get("data", {}).get("hasMore", False)

        messages = [Message.from_api(item) for item in msg_list]
        return messages, has_more


def send_message(browser, conversation_index: int, text: str) -> str:
    """Send a message to a conversation by its list index.

    Returns the name of the contact the message was sent to.
    """
    with step("messenger", "send_message", page=browser.page):
        return browser.send_message(conversation_index, text)
