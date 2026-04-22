"""Ali CLI — Click entry point with all commands."""

import json
import os
import sys
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from ali_cli.config import (
    load_config,
    save_config,
    clear_session,
    session_exists,
    get_email,
    get_browser_use_api_key,
    SESSION_FILE,
)
from ali_cli.errors import start_run, log_error, AliError

console = Console()

# ── Exit codes ──────────────────────────────────────────────────────
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SESSION_EXPIRED = 2
EXIT_RECOVERY_FAILED = 3
EXIT_INFRA_ERROR = 4


def _get_browser(config=None, cdp_url=None):
    """Create a BrowserManager, reusing active cloud browser session if available.
    
    Checks ~/.ali-cli/browser-session.json first. If a live session exists,
    connects to it (no gateway config.patch needed). Otherwise falls back to
    local headless browser with saved cookies.
    """
    from ali_cli.browser import BrowserManager
    from ali_cli.session_manager import load_cloud_session as load_browser_session, _get_api_key, _check_session_alive

    if config is None:
        config = load_config()

    # If explicit cdp_url passed, use it
    if cdp_url:
        return BrowserManager(cdp_url=cdp_url)

    # Try reusing active cloud browser session
    saved = load_browser_session()
    if saved:
        api_key = _get_api_key()
        sid = saved.get("session_id")
        cdp = saved.get("cdp_url")
        if sid and cdp and api_key and _check_session_alive(api_key, sid):
            return BrowserManager(cdp_url=cdp)

    # Fallback to local headless with saved cookies
    return BrowserManager(
        headless=config.get("headless", True),
        timeout=config.get("timeout", 30000),
    )


def _handle_session_expired(as_json: bool, command: str = ""):
    """Standard handler for SessionExpiredError — prints message and exits 2."""
    log_error(command or "unknown", "session_check", "Session expired",
              hint="Run 'ali login' to refresh.")
    if as_json:
        click.echo(json.dumps({"error": "session_expired"}))
    else:
        console.print("[red]Session expired. Run 'ali login'.[/red]")
    sys.exit(EXIT_SESSION_EXPIRED)


def _handle_error(e: Exception, as_json: bool, command: str = ""):
    """Standard handler for generic errors — tries recovery, then exits."""
    from ali_cli.recovery import fingerprint_error, attempt_recovery

    error_str = str(e)
    pattern = fingerprint_error(error_str)

    if pattern and pattern.get("auto_recoverable"):
        log_error(command or "unknown", "error_handler", error_str,
                  hint=f"Known pattern: {pattern['id']}, attempting recovery...")
        success = attempt_recovery(pattern["recovery_action"], {})
        if success:
            # Recovery succeeded but we can't retry from here — inform the user
            if as_json:
                click.echo(json.dumps({"error": error_str, "recovery": pattern["id"], "recovered": True}))
            else:
                console.print(f"[yellow]Error recovered ({pattern['id']}). Please retry the command.[/yellow]")
            sys.exit(EXIT_RECOVERY_FAILED)

    log_error(command or "unknown", "error_handler", error_str,
              hint=pattern["hint"] if pattern else "")
    if as_json:
        click.echo(json.dumps({"error": error_str}))
    else:
        console.print(f"[red]{e}[/red]")
        if pattern and pattern.get("hint"):
            console.print(f"[dim]  Hint: {pattern['hint']}[/dim]")
    sys.exit(EXIT_ERROR)


def _load_api_key():
    """Load Browser Use API key from config, env, or ALI_CLI_HOME/.env."""
    return get_browser_use_api_key()


def _session_age():
    """Return human-readable session age, or None."""
    if not SESSION_FILE.exists():
        return None
    mtime = SESSION_FILE.stat().st_mtime
    age_s = datetime.now().timestamp() - mtime
    if age_s < 60:
        return f"{int(age_s)}s"
    elif age_s < 3600:
        return f"{int(age_s / 60)}m"
    else:
        return f"{int(age_s / 3600)}h {int((age_s % 3600) / 60)}m"


@click.group()
@click.version_option(version="0.1.0", prog_name="ali")
def cli():
    """Ali CLI — Alibaba.com buyer portal automation tool."""
    pass


# ── Login ────────────────────────────────────────────────────────────

@cli.command()
@click.option("--email", default=None, help="Login email (overrides config)")
def login(email):
    """Log in to Alibaba.

    Uses the Browser Use cloud browser to complete the OTP flow:
      1. Starts (or reuses) a cloud browser with your configured profile
      2. Visits alibaba.com to check for existing login cookies
      3. If expired, fills email → requests OTP → fetches from Gmail → pastes
      4. Saves cookies to ALI_CLI_HOME/state.json
      5. Stops the cloud browser (stops billing)
    """
    from ali_cli.session_manager import refresh_login

    config = load_config()
    try:
        email = get_email(email)
    except RuntimeError as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(EXIT_ERROR)

    try:
        refresh_login(console)
        # Persist the email we just used into config so future runs don't prompt
        if config.get("email") != email:
            config["email"] = email
            save_config(config)
        console.print("[bold green]✅ Logged in and session saved![/bold green]")
    except Exception as e:
        console.print(f"[bold red]Login failed: {e}[/bold red]")
        sys.exit(EXIT_ERROR)


# ── Status ───────────────────────────────────────────────────────────

@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def status(as_json):
    """Show session status: logged in, unread count, session age."""
    from ali_cli.browser import SessionExpiredError
    start_run("status")

    if not session_exists():
        if as_json:
            click.echo(json.dumps({"logged_in": False, "reason": "no session"}))
        else:
            console.print("[yellow]No saved session. Run 'ali login' first.[/yellow]")
        sys.exit(1)

    age = _session_age()

    try:
        with _get_browser() as bm:
            bm._ensure_on_messenger()

            data = bm.get_unread_summary()
            has_login = data.get("data", {}).get("hasLogin", False) if data else False
            unread = data.get("data", {}).get("unreadCount", 0) if data else 0
            conv_count = len(data.get("data", {}).get("list", [])) if data else 0

            bm.save_current_session()

            if as_json:
                click.echo(json.dumps({
                    "logged_in": has_login,
                    "unread_count": unread,
                    "conversations_with_unread": conv_count,
                    "session_age": age,
                }))
            else:
                status_text = "[green]Active[/green]" if has_login else "[red]Expired[/red]"
                console.print(Panel(
                    f"  Login: {status_text}\n"
                    f"  Unread messages: [bold]{unread}[/bold]\n"
                    f"  Unread conversations: {conv_count}\n"
                    f"  Session age: {age or 'unknown'}",
                    title="Ali CLI Status",
                ))

    except SessionExpiredError:
        _handle_session_expired(as_json, "status")
    except RuntimeError as e:
        _handle_error(e, as_json, "status")


# ── Health check ─────────────────────────────────────────────────────

@cli.command()
@click.option("--quick", is_flag=True, help="Quick check — cookie age only, no browser launch.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def health(quick, as_json):
    """Session health check: cookie age, login status, API reachability."""
    from ali_cli.session_manager import state_age_hours
    from ali_cli.browser import SessionExpiredError
    start_run("health")

    age = state_age_hours()
    cookie_status = "missing"
    if age is not None:
        if age < 20:
            cookie_status = "fresh"
        elif age < 24:
            cookie_status = "aging"
        else:
            cookie_status = "expired"

    result = {
        "cookie_age_hours": round(age, 2) if age is not None else None,
        "cookie_status": cookie_status,
        "session_file_exists": session_exists(),
    }

    if quick:
        result["api_reachable"] = None
        result["logged_in"] = None
        if as_json:
            click.echo(json.dumps(result))
        else:
            console.print(Panel(
                f"  Cookie age: {age:.1f}h ({cookie_status})" if age else "  No saved cookies",
                title="Ali CLI Health (quick)",
            ))
        sys.exit(0 if cookie_status in ("fresh", "aging") else 1)

    # Full health check — launch browser, hit unread API
    try:
        with _get_browser() as bm:
            bm._ensure_on_messenger()
            data = bm.get_unread_summary()

            has_login = data.get("data", {}).get("hasLogin", False) if data else False
            unread = data.get("data", {}).get("unreadCount", 0) if data else 0

            result["logged_in"] = has_login
            result["api_reachable"] = True
            result["unread_count"] = unread

            bm.save_current_session()

    except SessionExpiredError:
        result["logged_in"] = False
        result["api_reachable"] = True
        if as_json:
            click.echo(json.dumps(result))
        else:
            console.print("[red]Session expired. Run 'ali login'.[/red]")
        sys.exit(2)
    except Exception as e:
        result["logged_in"] = False
        result["api_reachable"] = False
        result["error"] = str(e)

    if as_json:
        click.echo(json.dumps(result))
    else:
        login_str = "[green]Yes[/green]" if result.get("logged_in") else "[red]No[/red]"
        api_str = "[green]Yes[/green]" if result.get("api_reachable") else "[red]No[/red]"
        age_str = f"{age:.1f}h ({cookie_status})" if age is not None else "No cookies"
        console.print(Panel(
            f"  Cookie age: {age_str}\n"
            f"  Logged in: {login_str}\n"
            f"  API reachable: {api_str}\n"
            f"  Unread messages: {result.get('unread_count', '?')}",
            title="Ali CLI Health",
        ))

    if not result.get("logged_in") or not result.get("api_reachable"):
        sys.exit(2 if not result.get("logged_in") else 1)


# ── Conversations ────────────────────────────────────────────────────

@cli.command()
@click.option("--unread", is_flag=True, help="Show only unread conversations.")
@click.option("--limit", default=50, help="Max conversations to show.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def conversations(unread, limit, as_json):
    """List conversations (reliable, with API + DOM fallback)."""
    from ali_cli.messenger import get_conversations as _get_convs, get_unread_summary
    from ali_cli.browser import SessionExpiredError
    start_run("conversations")

    try:
        with _get_browser() as bm:
            if unread:
                summary = get_unread_summary(bm)
                convs = summary.conversations
            else:
                convs = _get_convs(bm, limit=limit)

            bm.save_current_session()

            if as_json:
                click.echo(json.dumps([c.to_dict() for c in convs], indent=2))
                return

            if not convs:
                console.print("[yellow]No conversations found.[/yellow]")
                return

            table = Table(title="Conversations")
            table.add_column("#", style="dim", width=4)
            table.add_column("Contact", style="cyan", max_width=20)
            table.add_column("Company", style="blue", max_width=30)
            table.add_column("Time", style="dim", width=16)
            table.add_column("Unread", justify="right", width=7)

            for i, c in enumerate(convs):
                unread_val = str(c.unread) if c.unread else ""
                unread_style = "bold red" if c.unread else ""
                table.add_row(
                    str(i + 1),
                    c.name or "?",
                    c.company_name or "",
                    c.time or "",
                    f"[{unread_style}]{unread_val}[/{unread_style}]" if unread_val else "",
                )

            console.print(table)

    except SessionExpiredError:
        _handle_session_expired(as_json, "conversations")
    except RuntimeError as e:
        _handle_error(e, as_json, "conversations")


# ── Messages ─────────────────────────────────────────────────────────

@cli.command()
@click.option("--unread", is_flag=True, help="Show only unread conversations.")
@click.option("--limit", default=30, help="Max conversations to show.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def messages(unread, limit, as_json):
    """List recent conversations from Alibaba Messenger."""
    from ali_cli.messenger import get_conversations, get_unread_summary
    from ali_cli.browser import SessionExpiredError
    start_run("messages")

    try:
        with _get_browser() as bm:
            if unread:
                summary = get_unread_summary(bm)
                conversations = summary.conversations
            else:
                conversations = get_conversations(bm, limit=limit)

            bm.save_current_session()

            if as_json:
                click.echo(json.dumps([c.to_dict() for c in conversations], indent=2))
                return

            if not conversations:
                console.print("[yellow]No conversations found.[/yellow]")
                return

            table = Table(title="Alibaba Messages")
            table.add_column("#", style="dim", width=4)
            table.add_column("Contact", style="cyan", max_width=20)
            table.add_column("Company", style="blue", max_width=30)
            table.add_column("Preview", max_width=40)
            table.add_column("Time", style="dim", width=16)
            table.add_column("Unread", justify="right", width=7)

            for i, c in enumerate(conversations):
                unread_val = str(c.unread) if c.unread else ""
                unread_style = "bold red" if c.unread else ""
                table.add_row(
                    str(i + 1),
                    c.name or "?",
                    c.company_name or "",
                    (c.preview or "")[:40],
                    c.time or "",
                    f"[{unread_style}]{unread_val}[/{unread_style}]"
                    if unread_val
                    else "",
                )

            console.print(table)

    except SessionExpiredError:
        _handle_session_expired(as_json, "messages")
    except RuntimeError as e:
        _handle_error(e, as_json, "messages")


# ── Read conversation ────────────────────────────────────────────────

@cli.command("read")
@click.argument("index", required=False, type=int, default=None)
@click.option("--name", default=None, help="Look up conversation by supplier name (fuzzy match).")
@click.option("--count", default=20, help="Number of messages to fetch.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def read_thread(index, name, count, as_json):
    """Read messages from a conversation (by # or --name)."""
    from ali_cli.messenger import get_conversation_by_index, get_conversation_by_name, get_messages
    from ali_cli.browser import SessionExpiredError
    start_run("read", {"name": name, "index": index})

    if index is None and name is None:
        console.print("[red]Provide a conversation number or --name 'supplier'[/red]")
        sys.exit(1)

    if index is not None and index < 1:
        console.print("[red]Index must be >= 1[/red]")
        sys.exit(1)

    try:
        with _get_browser() as bm:
            if name:
                conv, cid = get_conversation_by_name(bm, name)
            else:
                conv, cid = get_conversation_by_index(bm, index - 1)

            if not cid:
                if as_json:
                    click.echo(json.dumps({"error": "Could not determine conversation ID"}))
                else:
                    console.print("[red]Could not determine conversation ID.[/red]")
                sys.exit(1)

            msgs, has_more = get_messages(bm, cid, count=count)
            bm.save_current_session()

            if as_json:
                click.echo(json.dumps({
                    "conversation": conv.to_dict(),
                    "messages": [m.to_dict() for m in msgs],
                    "has_more": has_more,
                }, indent=2))
                return

            if not msgs:
                console.print("[yellow]No messages found in this conversation.[/yellow]")
                return

            # Header
            console.print(Panel(
                f"[cyan]{conv.name}[/cyan] — {conv.company_name}",
                subtitle=f"{'more messages available' if has_more else 'end of history'}",
            ))

            # Messages (reverse to show oldest first)
            for msg in reversed(msgs):
                if msg.msg_type == 9999:
                    continue  # Skip system messages

                style = "green" if msg.is_self else "cyan"
                label = "You" if msg.is_self else (conv.name or "Supplier")
                time_str = msg.time or ""

                console.print(f"[{style}]{label}[/{style}]  [dim]{time_str}[/dim]")
                console.print(f"  {msg.text}")
                if msg.image_url:
                    console.print(f"  [dim]📷 {msg.image_url[:80]}...[/dim]")
                if msg.file_url:
                    console.print(f"  [dim]📎 {msg.file_url[:80]}...[/dim]")
                console.print()

    except SessionExpiredError:
        _handle_session_expired(as_json, "read")
    except (RuntimeError, IndexError, ValueError) as e:
        _handle_error(e, as_json, "read")


# ── Send message ─────────────────────────────────────────────────────

@cli.command()
@click.argument("index", type=int)
@click.argument("text")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def send(index, text, as_json):
    """Send a message to a conversation (by # from 'ali messages')."""
    from ali_cli.browser import SessionExpiredError
    start_run("send", {"index": index})

    if index < 1:
        console.print("[red]Index must be >= 1[/red]")
        sys.exit(1)

    try:
        with _get_browser() as bm:
            name = bm.send_message(index - 1, text)
            bm.save_current_session()

            if as_json:
                click.echo(json.dumps({"sent": True, "to": name, "text": text}))
            else:
                console.print(f"[green]Message sent to {name or 'conversation #' + str(index)}[/green]")

    except SessionExpiredError:
        _handle_session_expired(as_json, "send")
    except RuntimeError as e:
        _handle_error(e, as_json, "send")


# ── Download images/files ─────────────────────────────────────────

@cli.command("download")
@click.argument("index", required=False, type=int, default=None)
@click.option("--name", default=None, help="Look up conversation by supplier name (fuzzy match).")
@click.option("--type", "media_type", type=click.Choice(["image", "file", "all"]), default="all", help="Filter: image, file, or all.")
@click.option("--output-dir", default="./downloads", help="Directory to save files.")
@click.option("--latest", default=50, help="Scan this many recent messages for media.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def download(index, name, media_type, output_dir, latest, as_json):
    """Download images/files from a conversation."""
    from ali_cli.messenger import get_conversation_by_index, get_conversation_by_name, get_messages
    from ali_cli.browser import SessionExpiredError
    start_run("download", {"name": name, "index": index})

    if index is None and name is None:
        console.print("[red]Provide a conversation number or --name 'supplier'[/red]")
        sys.exit(1)

    if index is not None and index < 1:
        console.print("[red]Index must be >= 1[/red]")
        sys.exit(1)

    try:
        with _get_browser() as bm:
            if name:
                conv, cid = get_conversation_by_name(bm, name)
            else:
                conv, cid = get_conversation_by_index(bm, index - 1)

            if not cid:
                _handle_error(RuntimeError("Could not determine conversation ID"), as_json)

            msgs, _ = get_messages(bm, cid, count=latest)

            # Filter to media messages
            type_map = {"image": [60], "file": [53], "all": [60, 53]}
            wanted_types = type_map[media_type]
            media_msgs = [m for m in msgs if m.msg_type in wanted_types]

            if not media_msgs:
                if as_json:
                    click.echo(json.dumps({"downloaded": [], "count": 0}))
                else:
                    console.print("[yellow]No downloadable media found in recent messages.[/yellow]")
                return

            # Download each
            safe_name = (conv.name or "supplier").replace(" ", "_")[:30]
            downloaded = []
            for i, msg in enumerate(media_msgs):
                url = msg.image_url or msg.file_url
                if not url:
                    continue

                if msg.file_name:
                    fname = f"{safe_name}_{msg.time[:10] if msg.time else 'unknown'}_{i}_{msg.file_name}"
                else:
                    ext = msg.file_type or "jpg"
                    fname = f"{safe_name}_{msg.time[:10] if msg.time else 'unknown'}_{i}.{ext}"

                out_path = os.path.join(output_dir, fname)
                try:
                    bm.download_file(url, out_path)
                    downloaded.append({
                        "path": out_path,
                        "type": "image" if msg.msg_type == 60 else "file",
                        "size": msg.media_size,
                        "name": msg.file_name or fname,
                    })
                    if not as_json:
                        console.print(f"  [green]✓[/green] {out_path}")
                except Exception as e:
                    if not as_json:
                        console.print(f"  [red]✗[/red] Failed: {e}")

            bm.save_current_session()

            if as_json:
                click.echo(json.dumps({"downloaded": downloaded, "count": len(downloaded)}, indent=2))
            else:
                console.print(f"\n[green]Downloaded {len(downloaded)} file(s)[/green]")

    except SessionExpiredError:
        _handle_session_expired(as_json, "download")
    except (RuntimeError, IndexError, ValueError) as e:
        _handle_error(e, as_json, "download")


# ── Send image ───────────────────────────────────────────────────────

@cli.command("send-image")
@click.argument("index", type=int)
@click.argument("image_path", type=click.Path(exists=True))
@click.option("--caption", default="", help="Optional text caption to send after image.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def send_image(index, image_path, caption, as_json):
    """Send an image to a conversation (by # from 'ali conversations')."""
    from ali_cli.browser import SessionExpiredError

    if index < 1:
        console.print("[red]Index must be >= 1[/red]")
        sys.exit(1)

    try:
        with _get_browser() as bm:
            contact_name = bm.send_file(index - 1, image_path, caption=caption)
            bm.save_current_session()

            if as_json:
                click.echo(json.dumps({"sent": True, "to": contact_name, "file": image_path, "type": "image"}))
            else:
                console.print(f"[green]Image sent to {contact_name or 'conversation #' + str(index)}[/green]")

    except SessionExpiredError:
        _handle_session_expired(as_json, "send-image")
    except RuntimeError as e:
        _handle_error(e, as_json, "send-image")


# ── Send file ────────────────────────────────────────────────────────

@cli.command("send-file")
@click.argument("index", type=int)
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def send_file(index, file_path, as_json):
    """Send a file to a conversation (by # from 'ali conversations')."""
    from ali_cli.browser import SessionExpiredError

    if index < 1:
        console.print("[red]Index must be >= 1[/red]")
        sys.exit(1)

    try:
        with _get_browser() as bm:
            contact_name = bm.send_file(index - 1, file_path)
            bm.save_current_session()

            if as_json:
                click.echo(json.dumps({"sent": True, "to": contact_name, "file": file_path, "type": "file"}))
            else:
                console.print(f"[green]File sent to {contact_name or 'conversation #' + str(index)}[/green]")

    except SessionExpiredError:
        _handle_session_expired(as_json, "send-file")
    except RuntimeError as e:
        _handle_error(e, as_json, "send-file")


# ── RFQ list ─────────────────────────────────────────────────────────

@cli.command()
@click.option("--page", "page_num", default=1, help="Page number.")
@click.option("--limit", default=20, help="Items per page.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def rfqs(page_num, limit, as_json):
    """List RFQs (Requests for Quotation) with quote counts."""
    from ali_cli.rfq import get_rfq_list
    from ali_cli.browser import SessionExpiredError
    start_run("rfqs")

    try:
        with _get_browser() as bm:
            rfq_list, total, unread_quotes = get_rfq_list(
                bm, page_num=page_num, page_size=limit
            )
            bm.save_current_session()

            if as_json:
                click.echo(json.dumps({
                    "total": total,
                    "unread_quotations": unread_quotes,
                    "page": page_num,
                    "rfqs": [r.to_dict() for r in rfq_list],
                }, indent=2))
                return

            if not rfq_list:
                console.print("[yellow]No RFQs found.[/yellow]")
                return

            console.print(
                f"[dim]Total RFQs: {total} | Unread quotations: {unread_quotes}[/dim]\n"
            )

            table = Table(title="RFQs")
            table.add_column("#", style="dim", width=4)
            table.add_column("ID", style="dim", width=12)
            table.add_column("Product", style="cyan", max_width=50)
            table.add_column("Status", width=10)
            table.add_column("Date", style="dim", width=12)
            table.add_column("Qty", justify="right", width=10)
            table.add_column("Quotes", justify="right", width=7)
            table.add_column("Unread", justify="right", width=7)

            for i, r in enumerate(rfq_list):
                status_style = {
                    "Approved": "green",
                    "Closed": "dim",
                    "Pending": "yellow",
                    "Expired": "red",
                }.get(r.status, "")

                table.add_row(
                    str(i + 1 + (page_num - 1) * limit),
                    str(r.id),
                    r.subject[:50] if r.subject else "?",
                    f"[{status_style}]{r.status}[/{status_style}]" if status_style else r.status,
                    r.date,
                    f"{r.quantity:,} {r.quantity_unit}" if r.quantity else "",
                    str(r.quotes_received),
                    str(r.unread_quotes) if r.unread_quotes else "",
                )

            console.print(table)

    except SessionExpiredError:
        _handle_session_expired(as_json, "rfqs")
    except RuntimeError as e:
        _handle_error(e, as_json, "rfqs")


# ── RFQ detail ───────────────────────────────────────────────────────

@cli.command("rfq")
@click.argument("rfq_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def rfq_detail(rfq_id, as_json):
    """View quote details for a specific RFQ (by ID)."""
    from ali_cli.rfq import get_rfq_by_id
    from ali_cli.browser import SessionExpiredError
    start_run("rfq", {"rfq_id": rfq_id})

    try:
        with _get_browser() as bm:
            rfq = get_rfq_by_id(bm, rfq_id)
            bm.save_current_session()

            if not rfq:
                if as_json:
                    click.echo(json.dumps({"error": f"RFQ {rfq_id} not found"}))
                else:
                    console.print(f"[red]RFQ {rfq_id} not found.[/red]")
                sys.exit(1)

            if as_json:
                click.echo(json.dumps(rfq.to_dict(), indent=2))
                return

            # RFQ header
            console.print(Panel(
                f"[bold]{rfq.subject}[/bold]\n"
                f"  Status: {rfq.status} | Date: {rfq.date} | Expires: {rfq.expiry_date}\n"
                f"  Quantity: {rfq.quantity:,} {rfq.quantity_unit}\n"
                f"  Quotes received: {rfq.quotes_received} | Unread: {rfq.unread_quotes}",
                title=f"RFQ #{rfq.id}",
            ))

            if not rfq.quotes:
                console.print("[yellow]No quotes received yet.[/yellow]")
                return

            # Quotes table
            table = Table(title="Quotations")
            table.add_column("#", style="dim", width=4)
            table.add_column("Supplier", style="cyan", max_width=25)
            table.add_column("Company", style="blue", max_width=35)
            table.add_column("Quote ID", style="dim", width=12)
            table.add_column("Date", style="dim", width=12)
            table.add_column("Read", width=5)

            for i, q in enumerate(rfq.quotes):
                name = f"{q.first_name} {q.last_name}".strip()
                read_icon = "[green]Y[/green]" if q.read else "[red]N[/red]"
                table.add_row(
                    str(i + 1),
                    name,
                    q.company_name[:35] if q.company_name else "",
                    str(q.quote_id),
                    q.modified,
                    read_icon,
                )

            console.print(table)

    except SessionExpiredError:
        _handle_session_expired(as_json, "rfq")
    except RuntimeError as e:
        _handle_error(e, as_json, "rfq")


# ── RFQ quote pricing (comparison page) ─────────────────────────────

@cli.command("rfq-quotes")
@click.argument("rfq_id", type=int)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def rfq_quotes(rfq_id, as_json):
    """Get quote pricing for a specific RFQ (scrapes comparison page)."""
    from ali_cli.rfq import get_rfq_quote_details, get_rfq_by_id
    from ali_cli.browser import SessionExpiredError
    start_run("rfq-quotes", {"rfq_id": rfq_id})

    try:
        with _get_browser() as bm:
            rfq = get_rfq_by_id(bm, rfq_id)
            details = get_rfq_quote_details(bm, rfq_id)
            bm.save_current_session()

            if as_json:
                click.echo(json.dumps({
                    "rfq_id": rfq_id,
                    "subject": rfq.subject if rfq else "",
                    "quotes": details,
                }, indent=2))
            else:
                if rfq:
                    console.print(f"[bold]{rfq.subject}[/bold]")
                if not details:
                    console.print("[yellow]No quote pricing found on comparison page.[/yellow]")
                else:
                    for i, q in enumerate(details):
                        price_str = f"${q['price']:.4f}/{q['unit']}" if q.get('price') is not None else "N/A"
                        product_str = f" | {q['product'][:60]}" if q.get('product') else ""
                        console.print(f"  {i+1}. {q['company'][:40]} — {price_str}{product_str}")

    except SessionExpiredError:
        _handle_session_expired(as_json, "rfq-quotes")
    except RuntimeError as e:
        _handle_error(e, as_json, "rfq-quotes")


# ── Session management ───────────────────────────────────────────────

@cli.command("keepalive")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def keepalive_cmd(as_json):
    """Refresh Alibaba cookies using local browser (no cloud browser needed)."""
    from ali_cli.session_manager import keepalive
    success = keepalive()
    if as_json:
        click.echo(json.dumps({"success": success}))
    elif not success:
        console.print("[yellow]Keepalive failed — may need 'ali login'[/yellow]")
    else:
        console.print("[green]Keepalive successful.[/green]")
    if not success:
        sys.exit(1)

@cli.command("browser")
@click.argument("action", type=click.Choice(["start", "stop", "status"]))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def browser_cmd(action, as_json):
    """Manage cloud browser session (start/stop/status)."""
    from ali_cli.session_manager import (
        start_cloud_browser, stop_cloud_browser,
        load_cloud_session, _get_api_key, _check_session_alive,
        state_age_hours,
    )

    if action == "start":
        try:
            cdp_url, session_id = start_cloud_browser()
            if as_json:
                click.echo(json.dumps({"status": "active", "session_id": session_id, "cdp_url": cdp_url}))
            else:
                console.print(f"[green]✅ Cloud browser active[/green]")
                console.print(f"  Session: {session_id[:12]}...")
        except Exception as e:
            if as_json:
                click.echo(json.dumps({"status": "error", "error": str(e)}))
            else:
                console.print(f"[red]{e}[/red]")
            sys.exit(1)

    elif action == "stop":
        stop_cloud_browser()
        if as_json:
            click.echo(json.dumps({"status": "stopped"}))
        else:
            console.print("[green]Cloud browser stopped.[/green]")

    elif action == "status":
        age = state_age_hours()
        saved = load_cloud_session()
        alive = False
        if saved:
            api_key = _get_api_key()
            alive = _check_session_alive(api_key, saved.get("session_id", "")) if api_key else False

        if as_json:
            click.echo(json.dumps({
                "cookie_age_hours": round(age, 2) if age is not None else None,
                "cloud_browser_active": alive,
                "session_id": saved.get("session_id") if saved else None,
            }))
        else:
            if age is not None:
                console.print(f"  Cookie age: {age:.1f} hours {'[green](fresh)[/green]' if age < 20 else '[yellow](getting old)[/yellow]' if age < 24 else '[red](likely expired)[/red]'}")
            else:
                console.print("  [yellow]No saved cookies[/yellow]")
            console.print(f"  Cloud browser: {'[green]active[/green]' if alive else '[dim]stopped[/dim]'}")


# ── Post RFQ ─────────────────────────────────────────────────────────

@cli.command("post-rfq")
@click.option("--subject", required=True, help="RFQ description/subject text.")
@click.option("--quantity", default=0, type=int, help="Sourcing quantity (e.g. 10000).")
@click.option("--unit", default="pieces", help="Quantity unit (default: pieces).")
@click.option("--attach", default=None, type=click.Path(), help="File to attach (xlsx, pdf, etc.).")
@click.option("--description", default=None, help="Override AI-generated description.")
@click.option("--no-ai", is_flag=True, help="Disable AI auto-generation.")
@click.option("--dry-run", is_flag=True, help="Stop before final submit, show form data.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def post_rfq_cmd(subject, quantity, unit, attach, description, no_ai, dry_run, as_json):
    """Post a new RFQ on Alibaba.com.

    Requires a cloud browser session (auto-started). The flow:
    1. Upload attachment (if provided)
    2. Fill subject text → Alibaba AI generates detailed RFQ
    3. Fill quantity and submit

    Example:
        ali post-rfq --subject "Stand-up pouches with zipper, matte, 4oz" \\
            --quantity 10000 --attach ~/rfq.xlsx
    """
    from ali_cli.session_manager import ensure_browser_session, ensure_logged_in_browser, stop_cloud_browser
    from ali_cli.rfq_post import post_rfq

    try:
        console.print("Starting cloud browser...", style="bold")
        cdp_url, session_id = ensure_browser_session(timeout_min=30)
        console.print(f"  Session: {session_id[:12]}...")

        # Ensure we're logged in
        console.print("  Checking login state...")
        try:
            bm = ensure_logged_in_browser(cdp_url, target_url="https://rfq.alibaba.com/rfq/profession.htm")
            bm.close()
        except Exception:
            console.print("  [yellow]Login check failed — proceeding anyway[/yellow]")

        result = post_rfq(
            cdp_url=cdp_url,
            subject=subject,
            quantity=quantity,
            unit=unit,
            attachment=attach,
            description=description,
            auto_generate=not no_ai,
            dry_run=dry_run,
            console=None if as_json else console,
        )

        if as_json:
            click.echo(json.dumps(result, indent=2, default=str))
        else:
            if result.get("success"):
                console.print("\n[bold green]✅ RFQ posted successfully![/bold green]")
                if result.get("form_data"):
                    fd = result["form_data"]
                    console.print(f"  [bold]Product:[/bold] {fd.get('product_name', '?')}")
                    console.print(f"  [bold]Category:[/bold] {fd.get('category', '?')}")
                    console.print(f"  [bold]Quantity:[/bold] {fd.get('quantity', '?')}")
                    if fd.get('detail'):
                        console.print(f"  [bold]Detail:[/bold] {fd['detail'][:200]}...")
                if result.get("rfq_id"):
                    console.print(f"  [bold]RFQ ID:[/bold] {result['rfq_id']}")
                if result.get("url"):
                    console.print(f"  [bold]URL:[/bold] {result['url']}")
                if result.get("dry_run"):
                    console.print("\n  [yellow]DRY RUN — not actually submitted[/yellow]")
            else:
                console.print(f"\n[red]RFQ posting failed: {result.get('error', 'unknown')}[/red]")
                sys.exit(1)

    except Exception as e:
        if as_json:
            click.echo(json.dumps({"error": str(e)}))
        else:
            console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(1)


# ── Monitor ──────────────────────────────────────────────────────────

@cli.command("monitor")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def monitor_cmd(as_json):
    """Full packaging check in ONE browser session (fast).

    Checks all unread messages + RFQ quotes in a single browser launch.
    ~30s total vs 200s+ for running ali messages/rfqs separately.
    Use this in crons instead of chaining multiple commands.
    """
    from ali_cli.monitor import run_monitor

    result = run_monitor(console=None if as_json else console)

    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        if result["errors"]:
            sys.exit(1)
        return

    # Pretty terminal output
    age = result.get("session_age_hours")
    age_str = f"{age}h" if age is not None else "unknown"
    login_color = "green" if result["logged_in"] else "red"
    console.print(f"\n🕐 Session: {age_str} old | "
                  f"Logged in: [{login_color}]{result['logged_in']}[/{login_color}] | "
                  f"⏱ {result['elapsed_seconds']}s")

    convos = result["unread_conversations"]
    console.print(f"\n[bold]📨 Unread conversations: {len(convos)}[/bold]")
    for c in convos:
        icon = "🖼" if c["has_images"] else "💬"
        latest = c["messages"][0]["text"][:70] if c["messages"] else "—"
        console.print(f"  {icon} [cyan]{c['name']}[/cyan] ({c['company'][:30]})"
                      f" — [bold]{c['unread']}[/bold] unread")
        console.print(f"     Latest: {latest}")

    rfqs = result["rfqs"]
    console.print(f"\n[bold]📋 RFQs: {rfqs['total']} total, "
                  f"{rfqs['unread_quotes']} unread quotes[/bold]")
    for r in rfqs["active"]:
        unread_badge = f"[red]{r['unread_quotes']} new[/red]" if r["unread_quotes"] else ""
        console.print(f"  • {r['subject'][:60]} "
                      f"({r['quotes_received']} quotes {unread_badge})")

    if result["errors"]:
        console.print(f"\n[yellow]⚠ Errors: {result['errors']}[/yellow]")
        sys.exit(1)


# ── Doctor ───────────────────────────────────────────────────────────

@cli.command("doctor")
@click.option("--fix", is_flag=True, help="Auto-repair failures by spawning a coding agent.")
@click.option("--fast", is_flag=True, help="Fast mode — cookie check only, no API calls.")
@click.option("--skip-post", is_flag=True, help="Skip the slow post-rfq dry-run test.")
@click.option("--analyze", is_flag=True, help="Analyze error patterns and recovery stats.")
@click.option("--heal", is_flag=True, help="Attempt auto-recovery for known patterns.")
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON.")
@click.option("--log-issue", "log_issue_desc", default=None, help="Log a new issue to ALI_CLI_HOME/issues.md.")
@click.option("--error", "issue_error", default="", help="Error message for --log-issue.")
@click.option("--fix-desc", "issue_fix", default="", help="Fix description for --log-issue.")
@click.option("--command", "issue_cmd", default="", help="Command for --log-issue.")
def doctor(fix, fast, skip_post, analyze, heal, as_json,
           log_issue_desc, issue_error, issue_fix, issue_cmd):
    """Run self-tests and optionally auto-repair failures.

    \b
    Examples:
      ali doctor                    # Full test suite
      ali doctor --fast             # Quick cookie check only
      ali doctor --skip-post        # All tests except slow RFQ post test
      ali doctor --fix              # Auto-repair any failures
      ali doctor --analyze          # Error frequency, recovery stats, skill health
      ali doctor --heal             # Attempt auto-recovery for known patterns
      ali doctor --log-issue "send broke" --error "timeout"
    """
    from ali_cli.doctor import run_doctor, log_issue, run_analyze, run_heal

    # Log-issue mode
    if log_issue_desc:
        issue_id = log_issue(
            description=log_issue_desc,
            error=issue_error,
            fix_desc=issue_fix,
            command=issue_cmd,
        )
        if issue_id:
            console.print(f"[green]Logged {issue_id} to issues.md[/green]")
        else:
            console.print("[red]Could not write issue log.[/red]")
        return

    # Analyze mode
    if analyze:
        analysis = run_analyze()
        if as_json:
            click.echo(json.dumps(analysis, indent=2))
            return

        es = analysis["error_summary"]
        console.print(Panel(
            f"Total errors (7d): [bold]{es['total_errors']}[/bold]\n"
            f"Known patterns: {analysis['known_patterns_count']}",
            title="Error Analysis",
        ))

        if es["by_command"]:
            table = Table(title="Errors by Command")
            table.add_column("Command", style="cyan")
            table.add_column("Count", justify="right")
            for cmd, cnt in es["by_command"].items():
                table.add_row(cmd, str(cnt))
            console.print(table)

        if es["by_step"]:
            table = Table(title="Errors by Step")
            table.add_column("Step", style="yellow")
            table.add_column("Count", justify="right")
            for stp, cnt in list(es["by_step"].items())[:10]:
                table.add_row(stp, str(cnt))
            console.print(table)

        if analysis["recurring_patterns"]:
            console.print("\n[bold red]Recurring Patterns (3+ occurrences):[/bold red]")
            for err, cnt in analysis["recurring_patterns"].items():
                console.print(f"  [{cnt}x] {err}")

        rs = analysis["recovery_stats"]
        if rs["by_pattern"]:
            table = Table(title="Recovery Stats")
            table.add_column("Pattern", style="cyan")
            table.add_column("Attempts", justify="right")
            table.add_column("Success", justify="right")
            table.add_column("Rate", justify="right")
            for pid, stats in rs["by_pattern"].items():
                rate_color = "green" if stats["rate"] > 0.7 else "yellow" if stats["rate"] > 0.3 else "red"
                table.add_row(
                    pid, str(stats["attempts"]), str(stats["successes"]),
                    f"[{rate_color}]{stats['rate']:.0%}[/{rate_color}]",
                )
            console.print(table)

        if analysis["skill_health"]:
            table = Table(title="Skill Health")
            table.add_column("Skill", style="cyan")
            table.add_column("Runs", justify="right")
            table.add_column("Success", justify="right")
            table.add_column("Failure", justify="right")
            for skill, stats in analysis["skill_health"].items():
                table.add_row(
                    skill, str(stats["runs"]),
                    f"[green]{stats['success']}[/green]",
                    f"[red]{stats['failure']}[/red]" if stats["failure"] else "0",
                )
            console.print(table)
        return

    # Heal mode
    if heal:
        heal_result = run_heal()
        if as_json:
            click.echo(json.dumps(heal_result, indent=2))
            return

        console.print(Panel(
            f"Attempted: {heal_result['attempted']} | "
            f"Succeeded: [green]{heal_result['succeeded']}[/green]",
            title="Auto-Heal Results",
        ))
        for r in heal_result["results"]:
            icon = "[green]✓[/green]" if r["success"] else "[red]✗[/red]"
            console.print(f"  {icon} {r['pattern']} → {r['action']}")
        return

    # Standard doctor run
    result = run_doctor(fast_only=fast, skip_post=skip_post, auto_fix=fix)

    if as_json:
        click.echo(json.dumps(result, indent=2))
    elif not result["healthy"]:
        sys.exit(EXIT_ERROR)


# ── Logs ──────────────────────────────────────────────────────────────

@cli.command("logs")
@click.option("--errors", "errors_only", is_flag=True, help="Show recent errors only.")
@click.option("--run", "run_id", default=None, help="Show specific run by ID.")
@click.option("--since", "since_hours", default=None, type=float, help="Last N hours of errors.")
@click.option("--command", "cmd_filter", default=None, help="Filter by command name.")
@click.option("--limit", default=20, help="Max entries to show.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def logs_cmd(errors_only, run_id, since_hours, cmd_filter, limit, as_json):
    """View run traces and error logs.

    \b
    Examples:
      ali logs                    # Latest run trace (step by step)
      ali logs --errors           # Recent errors only
      ali logs --run abc123       # Specific run by ID
      ali logs --since 24         # Last 24 hours of errors
      ali logs --command monitor  # Filter by command name
    """
    from ali_cli.errors import get_recent_errors, get_run_trace

    if errors_only or since_hours is not None:
        entries = get_recent_errors(
            limit=limit,
            since_hours=since_hours,
            command_filter=cmd_filter,
        )
        if as_json:
            click.echo(json.dumps(entries, indent=2))
            return

        if not entries:
            console.print("[green]No errors found.[/green]")
            return

        table = Table(title="Recent Errors")
        table.add_column("Time", style="dim", width=20)
        table.add_column("Run", style="dim", width=8)
        table.add_column("Command", style="cyan", width=12)
        table.add_column("Step", style="yellow", width=25)
        table.add_column("Error", max_width=50)

        for e in entries:
            ts = e.get("ts", "")[:19].replace("T", " ")
            table.add_row(
                ts, e.get("run_id", "")[:8],
                e.get("command", ""), e.get("step", ""),
                str(e.get("error", ""))[:50],
            )
        console.print(table)
        return

    # Default: show run trace
    entries = get_run_trace(run_id=run_id)
    if cmd_filter:
        entries = [e for e in entries if e.get("command") == cmd_filter]

    if as_json:
        click.echo(json.dumps(entries, indent=2))
        return

    if not entries:
        console.print("[yellow]No run data found. Run an ali command first.[/yellow]")
        return

    # Show run header
    first = entries[0] if entries else {}
    rid = first.get("run_id", "?")
    cmd = first.get("command", "?")
    console.print(Panel(f"Run [bold]{rid}[/bold] — command: [cyan]{cmd}[/cyan]",
                        title="Run Trace"))

    table = Table()
    table.add_column("Time", style="dim", width=20)
    table.add_column("Step", style="cyan", width=30)
    table.add_column("Status", width=8)
    table.add_column("Details", max_width=50)

    for e in entries:
        ts = e.get("ts", "")[:19].replace("T", " ")
        status = e.get("status", "")
        status_style = {"ok": "green", "error": "red", "started": "blue"}.get(status, "")
        details = ""
        if e.get("details"):
            d = e["details"]
            if isinstance(d, dict):
                details = ", ".join(f"{k}={v}" for k, v in d.items() if v)[:50]
            else:
                details = str(d)[:50]
        if e.get("url"):
            details = (details + " " if details else "") + f"[dim]{e['url'][:40]}[/dim]"

        table.add_row(
            ts, e.get("step", ""),
            f"[{status_style}]{status}[/{status_style}]" if status_style else status,
            details,
        )

    console.print(table)


# ── Report ────────────────────────────────────────────────────────────

@cli.command("report")
@click.option("--skill", required=True, help="Skill name (e.g. alibaba-packaging-monitor).")
@click.option("--status", "report_status", required=True,
              type=click.Choice(["success", "failure", "partial"]), help="Execution status.")
@click.option("--steps-ok", default=0, type=int, help="Number of successful steps.")
@click.option("--steps-failed", default=0, type=int, help="Number of failed steps.")
@click.option("--duration", default=0, type=float, help="Duration in seconds.")
@click.option("--issues", default=None, help="JSON array of issue strings.")
@click.option("--improvements", default=None, help="JSON array of improvement strings.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def report_cmd(skill, report_status, steps_ok, steps_failed, duration, issues, improvements, as_json):
    """Log a skill execution report for cron analysis.

    \b
    Example:
      ali report --skill alibaba-packaging-monitor --status success \\
          --steps-ok 5 --steps-failed 0 --duration 30
    """
    from ali_cli.errors import log_skill_report

    issues_list = json.loads(issues) if issues else []
    improvements_list = json.loads(improvements) if improvements else []

    log_skill_report(
        skill=skill,
        status=report_status,
        steps_ok=steps_ok,
        steps_failed=steps_failed,
        duration=duration,
        issues=issues_list,
        improvements=improvements_list,
    )

    result = {
        "logged": True,
        "skill": skill,
        "status": report_status,
        "steps_ok": steps_ok,
        "steps_failed": steps_failed,
    }

    if as_json:
        click.echo(json.dumps(result))
    else:
        console.print(f"[green]Report logged for {skill} ({report_status})[/green]")


# ── OTP Watcher ──────────────────────────────────────────────────────

@cli.command("otp-watch")
def otp_watch():
    """Start OTP watcher — polls Gmail and writes codes to ~/.ali-cli/latest-otp.txt."""
    from ali_cli.otp_watcher import main as watcher_main
    watcher_main()


# ── Logout ───────────────────────────────────────────────────────────

@cli.command()
@click.option("--keep-browser", is_flag=True, help="Keep cloud browser alive (only clear cookies).")
def logout(keep_browser):
    """Clear saved session, cookies, and stop cloud browser."""
    from ali_cli.session_manager import stop_browser_session as stop_cloud
    
    clear_session()
    if not keep_browser:
        stop_cloud()
        console.print("[green]Session + cloud browser stopped.[/green]")
    else:
        console.print("[green]Session cleared (browser still alive).[/green]")


# ── Config management ────────────────────────────────────────────────

@cli.group()
def config():
    """View or update ali-cli config (~/.ali-cli/config.json)."""
    pass


@config.command("show")
def config_show():
    """Show current config (API key redacted)."""
    cfg = load_config()
    display = dict(cfg)
    if display.get("browser_use_api_key"):
        display["browser_use_api_key"] = "***" + display["browser_use_api_key"][-4:]
    click.echo(json.dumps(display, indent=2))


@config.command("set-email")
@click.argument("email")
def config_set_email(email):
    """Set the Alibaba login email."""
    cfg = load_config()
    cfg["email"] = email
    save_config(cfg)
    console.print(f"[green]Email set to {email}[/green]")


@config.command("set-profile-id")
@click.argument("profile_id")
def config_set_profile_id(profile_id):
    """Set the Browser Use profile ID used for the login browser."""
    cfg = load_config()
    cfg["browser_use_profile_id"] = profile_id
    save_config(cfg)
    console.print(f"[green]Browser Use profile ID set.[/green]")


@config.command("set-api-key")
@click.argument("api_key")
def config_set_api_key(api_key):
    """Set the Browser Use API key."""
    cfg = load_config()
    cfg["browser_use_api_key"] = api_key
    save_config(cfg)
    console.print(f"[green]Browser Use API key set.[/green]")


@config.command("path")
def config_path():
    """Print the ali-cli config directory."""
    from ali_cli.config import get_home
    click.echo(str(get_home()))


# ── Entry point ──────────────────────────────────────────────────────

def main():
    cli()


if __name__ == "__main__":
    main()
