"""ali-cli — Alibaba.com buyer portal automation.

Public API for library usage:

    from ali_cli import BrowserManager, SessionExpiredError
    from ali_cli.config import load_config, get_email
    from ali_cli.messenger import get_unread_summary, get_conversations
    from ali_cli.rfq import get_rfq_list, get_rfq_by_id
    from ali_cli.session_manager import refresh_login, keepalive

For CLI usage, install and run `ali --help`.
"""

from ali_cli.browser import BrowserManager, SessionExpiredError

__version__ = "0.1.0"
__all__ = ["BrowserManager", "SessionExpiredError", "__version__"]
