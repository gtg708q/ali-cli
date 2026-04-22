"""Data models for Ali CLI."""

import json
from dataclasses import dataclass, field, asdict
from typing import Optional, List
from datetime import datetime


@dataclass
class Conversation:
    """A messenger conversation with a supplier."""
    name: str = ""
    company_name: str = ""
    preview: str = ""
    time: str = ""
    unread: int = 0
    account_id: int = 0
    login_id: str = ""
    cid: str = ""
    mute: bool = False
    blocked: bool = False
    portrait_url: str = ""
    chat_token: str = ""
    account_id_encrypt: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_unread_api(cls, item: dict) -> "Conversation":
        """Create from unread.htm API response item."""
        last_msg = item.get("lastMsg", {})
        ts = item.get("lastContactTime") or item.get("lastContactTimeLong", 0)
        time_str = ""
        if ts:
            try:
                time_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                time_str = str(ts)
        return cls(
            name=item.get("name", ""),
            company_name=item.get("companyName", ""),
            preview=last_msg.get("content", "")[:80] if last_msg else "",
            time=time_str,
            unread=item.get("unreadCount", 0),
            account_id=item.get("accountId", 0),
            login_id=item.get("loginId", ""),
            cid=item.get("cid", ""),
            mute=item.get("mute", False),
            blocked=item.get("block", False),
            portrait_url=item.get("fullPortrait", ""),
            chat_token=item.get("chatToken", ""),
            account_id_encrypt=item.get("accountIdEncrypt", ""),
        )

    @classmethod
    def from_full_list(cls, item: dict) -> "Conversation":
        """Create from window.__conversationListFullData__ item."""
        ts = item.get("lastContactTime", 0)
        time_str = ""
        if ts:
            try:
                time_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                time_str = str(ts)
        return cls(
            name=item.get("name", ""),
            company_name=item.get("companyName", ""),
            time=time_str,
            unread=item.get("unreadCount", 0),
            account_id=item.get("accountId", 0),
            login_id=item.get("loginId", ""),
            cid=item.get("cid", ""),
        )


@dataclass
class Message:
    """A single chat message."""
    text: str = ""
    sender: str = ""
    time: str = ""
    is_self: bool = False
    msg_type: int = 1
    message_id: str = ""
    sender_ali_id: int = 0
    send_time: int = 0
    image_url: str = ""       # Direct image URL (for messageType 60)
    file_url: str = ""        # File preview/download URL (for messageType 53)
    file_name: str = ""       # Original filename (for messageType 53)
    file_type: str = ""       # Extension type (jpg, xlsx, etc.)
    media_width: int = 0
    media_height: int = 0
    media_size: int = 0

    def to_dict(self):
        d = asdict(self)
        # Omit empty media fields for cleaner JSON output
        media_keys = ["image_url", "file_url", "file_name", "file_type",
                       "media_width", "media_height", "media_size"]
        for k in media_keys:
            if not d[k]:
                del d[k]
        return d

    @classmethod
    def from_api(cls, item: dict, self_ali_id: int = 0) -> "Message":
        """Create from listRecentMessage API response item."""
        ts = item.get("sendTime", 0)
        time_str = ""
        if ts:
            try:
                time_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                time_str = str(ts)

        content = item.get("content", "")
        msg_type = item.get("messageType", 1)

        # Media fields
        image_url = ""
        file_url = ""
        file_name = ""
        file_type = ""
        media_width = 0
        media_height = 0
        media_size = 0

        # For non-text messages, parse content JSON and provide readable summary
        if msg_type == 60:
            # Image message
            try:
                img = json.loads(content) if isinstance(content, str) else content
                image_url = img.get("url", "")
                file_type = img.get("suffix", "jpg")
                media_width = int(img.get("width", 0))
                media_height = int(img.get("height", 0))
                media_size = int(img.get("size", 0))
                display = f"[Image: {media_width}x{media_height} {file_type}]"
            except (json.JSONDecodeError, TypeError, ValueError):
                display = "[Image]"
        elif msg_type == 53:
            # File message
            try:
                raw = json.loads(content) if isinstance(content, str) else content
                params = raw.get("params", raw)
                file_url = params.get("url", "")
                file_name = params.get("name", "")
                file_type = params.get("extensionType", "")
                media_size = int(params.get("size", 0))
                size_str = _human_size(media_size) if media_size else "?"
                display = f"[File: {file_name} ({size_str})]"
            except (json.JSONDecodeError, TypeError, ValueError):
                display = "[File]"
        elif msg_type == 9999:
            display = "[System] " + content[:60]
        else:
            display = content

        sender_id = item.get("senderAliId", 0)
        return cls(
            text=display,
            sender="You" if item.get("messageSendType") == "send" else "",
            time=time_str,
            is_self=item.get("messageSendType") == "send",
            msg_type=msg_type,
            message_id=item.get("messageId", ""),
            sender_ali_id=sender_id,
            send_time=ts,
            image_url=image_url,
            file_url=file_url,
            file_name=file_name,
            file_type=file_type,
            media_width=media_width,
            media_height=media_height,
            media_size=media_size,
        )


def _human_size(size_bytes: int) -> str:
    """Convert bytes to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


@dataclass
class QuoteSeller:
    """A seller who quoted on an RFQ."""
    seller_member_id: str = ""
    seller_account_id: int = 0
    company_name: str = ""
    first_name: str = ""
    last_name: str = ""
    quote_id: int = 0
    modified: str = ""
    read: bool = False

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_api(cls, item: dict) -> "QuoteSeller":
        ts = item.get("gmtModified", 0)
        mod_str = ""
        if ts:
            try:
                mod_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                mod_str = str(ts)
        return cls(
            seller_member_id=item.get("sellerMemberId", ""),
            seller_account_id=item.get("sellerAccountId", 0),
            company_name=item.get("companyName", ""),
            first_name=item.get("firstName", ""),
            last_name=item.get("lastName", ""),
            quote_id=item.get("quoId", 0),
            modified=mod_str,
            read=item.get("read", False),
        )


@dataclass
class RFQ:
    """An RFQ (Request for Quotation)."""
    id: int = 0
    enc_id: str = ""
    subject: str = ""
    status: str = ""
    date: str = ""
    expiry_date: str = ""
    quotes_received: int = 0
    unread_quotes: int = 0
    quantity: int = 0
    quantity_unit: str = ""
    quotes: List[QuoteSeller] = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        return d

    @classmethod
    def from_api(cls, item: dict) -> "RFQ":
        sellers = [QuoteSeller.from_api(s) for s in item.get("quoSellerView", [])]
        return cls(
            id=item.get("id", 0),
            enc_id=item.get("encId", ""),
            subject=item.get("subject", ""),
            status=item.get("status", ""),
            date=item.get("gmtCreate", ""),
            expiry_date=item.get("expiryDate", ""),
            quotes_received=item.get("quotesReceived", 0),
            unread_quotes=item.get("unReadQuoSize", 0),
            quantity=item.get("quantity", 0),
            quantity_unit=item.get("quantityUnit", ""),
            quotes=sellers,
        )


@dataclass
class UnreadSummary:
    """Summary of unread messages."""
    has_login: bool = False
    unread_count: int = 0
    conversations: List[Conversation] = field(default_factory=list)

    def to_dict(self):
        return {
            "has_login": self.has_login,
            "unread_count": self.unread_count,
            "conversations": [c.to_dict() for c in self.conversations],
        }

    @classmethod
    def from_api(cls, data: dict) -> "UnreadSummary":
        convs = [Conversation.from_unread_api(item) for item in data.get("list", [])]
        return cls(
            has_login=data.get("hasLogin", False),
            unread_count=data.get("unreadCount", 0),
            conversations=convs,
        )
