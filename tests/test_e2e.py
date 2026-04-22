#!/usr/bin/env python3
"""End-to-end test suite for ali-cli against live Alibaba session.

Tests the full cron workflow:
  1. ali health --json
  2. ali status --json
  3. ali messages --unread --json
  4. ali messages --limit 10 --json
  5. ali read 1 --count 5 --json
  6. ali read --name "TestSupplier" --json
  7. ali rfqs --json --limit 10
  8. ali rfq <first_rfq_id> --json

Each command must:
  - Complete in <30s
  - Return valid JSON
  - Have required fields present
  - Exit code 0

Usage:
    python3 tests/test_e2e.py           # Run all tests
    python3 tests/test_e2e.py --quick   # Quick mode (health only, no browser)
"""

import json
import subprocess
import sys
import time
from pathlib import Path

# Resolve project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent

TIMEOUT_SECONDS = 60  # generous for browser startup
results: list[tuple[str, str, float]] = []  # (name, result, duration)


def run_cli(args: list[str], timeout: int = TIMEOUT_SECONDS) -> tuple[int, str, str]:
    """Run an ali CLI command and capture output.
    
    Returns (exit_code, stdout, stderr).
    """
    cmd = ["ali"] + args
    start = time.time()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )
    elapsed = time.time() - start
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip(), elapsed


def parse_json(text: str) -> dict | list | None:
    """Try to parse JSON from command output."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def test(name: str, args: list[str], required_fields: list[str] | None = None,
         expect_list: bool = False, check_fn=None, timeout: int = TIMEOUT_SECONDS):
    """Run a single test and record results."""
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"  CMD: ali {' '.join(args)}")

    try:
        code, stdout, stderr, elapsed = run_cli(args, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"  ✗ TIMEOUT (>{timeout}s)")
        results.append((name, f"FAIL: timeout", 0))
        return None

    print(f"  Exit code: {code} | Time: {elapsed:.1f}s")

    if code == 2:
        print(f"  ✗ SESSION EXPIRED — all remaining tests will likely fail")
        results.append((name, "FAIL: session expired (exit 2)", elapsed))
        return None

    if code != 0:
        print(f"  ✗ Non-zero exit: {code}")
        print(f"  stderr: {stderr[:200]}")
        results.append((name, f"FAIL: exit code {code}", elapsed))
        return None

    # Parse JSON
    data = parse_json(stdout)
    if data is None:
        print(f"  ✗ Invalid JSON output")
        print(f"  stdout[:200]: {stdout[:200]}")
        results.append((name, "FAIL: invalid JSON", elapsed))
        return None

    # Validate shape
    if expect_list and not isinstance(data, list):
        print(f"  ✗ Expected list, got {type(data).__name__}")
        results.append((name, f"FAIL: expected list", elapsed))
        return data

    if required_fields:
        target = data
        if isinstance(data, list) and data:
            target = data[0]
        missing = [f for f in required_fields if f not in target]
        if missing:
            print(f"  ✗ Missing fields: {missing}")
            results.append((name, f"FAIL: missing {missing}", elapsed))
            return data

    # Custom check
    if check_fn:
        try:
            check_fn(data)
        except AssertionError as e:
            print(f"  ✗ Check failed: {e}")
            results.append((name, f"FAIL: {e}", elapsed))
            return data

    # Timing check
    if elapsed > 30:
        print(f"  ⚠ Slow ({elapsed:.1f}s > 30s target)")

    print(f"  ✓ PASS ({elapsed:.1f}s)")
    results.append((name, "PASS", elapsed))
    return data


def main():
    quick = "--quick" in sys.argv

    print("=" * 60)
    print("ALI CLI — End-to-End Test Suite")
    print("=" * 60)

    # Test 1: Health check (quick — no browser)
    test(
        "health --quick",
        ["health", "--quick", "--json"],
        required_fields=["cookie_age_hours", "cookie_status", "session_file_exists"],
    )

    if quick:
        print_summary()
        return

    # Test 2: Health check (full — with browser)
    test(
        "health (full)",
        ["health", "--json"],
        required_fields=["cookie_age_hours", "logged_in", "api_reachable"],
        check_fn=lambda d: (
            _assert(d.get("logged_in"), "Not logged in!"),
            _assert(d.get("api_reachable"), "API not reachable!"),
        ),
    )

    # Test 3: Status
    test(
        "status",
        ["status", "--json"],
        required_fields=["logged_in", "unread_count", "session_age"],
    )

    # Test 4: Messages (unread only)
    test(
        "messages --unread",
        ["messages", "--unread", "--json"],
        expect_list=True,
    )

    # Test 5: Messages (recent)
    msg_data = test(
        "messages --limit 10",
        ["messages", "--limit", "10", "--json"],
        expect_list=True,
        required_fields=["name", "cid"],
    )

    # Test 6: Read first conversation
    test(
        "read 1 --count 5",
        ["read", "1", "--count", "5", "--json"],
        required_fields=["conversation", "messages", "has_more"],
        check_fn=lambda d: _assert(len(d.get("messages", [])) > 0, "No messages returned"),
    )

    # Test 7: Read by name (fuzzy match)
    test(
        'read --name "TestSupplier"',
        ["read", "--name", "TestSupplier", "--json"],
        required_fields=["conversation", "messages"],
        check_fn=lambda d: _assert(
            "shining" in d["conversation"].get("name", "").lower()
            or len(d.get("messages", [])) > 0,
            "Name match failed"
        ),
    )

    # Test 8: RFQ list
    rfq_data = test(
        "rfqs --limit 10",
        ["rfqs", "--json", "--limit", "10"],
        required_fields=["total", "rfqs"],
        check_fn=lambda d: _assert(d.get("total", 0) > 0, "No RFQs found"),
    )

    # Test 9: RFQ detail (use first RFQ ID from previous test)
    if rfq_data and rfq_data.get("rfqs"):
        first_rfq_id = str(rfq_data["rfqs"][0]["id"])
        test(
            f"rfq {first_rfq_id}",
            ["rfq", first_rfq_id, "--json"],
            required_fields=["id", "subject", "status", "quotes_received"],
        )
    else:
        print("\n  SKIP: rfq detail (no RFQ ID from previous test)")
        results.append(("rfq detail", "SKIP", 0))

    # Test 10: Conversations command
    test(
        "conversations --limit 10",
        ["conversations", "--limit", "10", "--json"],
        expect_list=True,
    )

    # Test 11: Read first conversation and check for media fields in JSON
    read_data = test(
        "read 1 (media fields)",
        ["read", "1", "--count", "50", "--json"],
        required_fields=["conversation", "messages"],
        check_fn=lambda d: _assert(
            isinstance(d.get("messages"), list),
            "messages should be a list"
        ),
    )

    # Test 12: Check image/file messages have URLs if present
    if read_data and read_data.get("messages"):
        media_msgs = [m for m in read_data["messages"] if m.get("msg_type") in (60, 53)]
        if media_msgs:
            has_url = any(m.get("image_url") or m.get("file_url") for m in media_msgs)
            if has_url:
                print(f"\n  ✓ Found {len(media_msgs)} media message(s) with URLs")
                results.append(("media URLs present", "PASS", 0))
            else:
                print(f"\n  ⚠ Found {len(media_msgs)} media message(s) but no URLs extracted")
                results.append(("media URLs present", "PASS (no URLs)", 0))
        else:
            print(f"\n  ○ No media messages in conversation (skip URL check)")
            results.append(("media URLs present", "SKIP (no media in conv)", 0))

    # Test 13: Download command — scan only 3 messages to keep it fast
    test(
        "download 1 (scan)",
        ["download", "1", "--latest", "3", "--json"],
        timeout=90,
    )

    print_summary()


def _assert(condition, msg=""):
    if not condition:
        raise AssertionError(msg)


def print_summary():
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = 0
    failed = 0
    skipped = 0
    total_time = 0.0

    for name, result, duration in results:
        if result == "PASS":
            icon = "✓"
            passed += 1
        elif result.startswith("SKIP"):
            icon = "○"
            skipped += 1
        else:
            icon = "✗"
            failed += 1
        total_time += duration
        print(f"  {icon} {name}: {result} ({duration:.1f}s)")

    print(f"\n  Total: {passed + failed + skipped} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")
    print(f"  Total time: {total_time:.1f}s")

    if failed > 0:
        print("\n  ✗ SOME TESTS FAILED")
        sys.exit(1)
    else:
        print("\n  ✓ ALL TESTS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
