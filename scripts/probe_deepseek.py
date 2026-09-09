"""Temporary diagnostic: probe DeepSeek streaming across HTTP transports.

Usage:
    uv run --project driver python scripts/probe_deepseek.py <API_KEY> [base_url] [model]

Tries four request variants against the given OpenAI-compatible chat/completions
endpoint and prints a PASS/FAIL line for each, so we can tell whether a
200-then-close is specific to httpx2, to trust_env (proxies), or universal.

This file is a throwaway diagnostic; delete it once the root cause is fixed.
"""

from __future__ import annotations

import os
import sys
import time

BASE = sys.argv[2] if len(sys.argv) > 2 else "https://api.deepseek.com"
MODEL = sys.argv[3] if len(sys.argv) > 3 else "deepseek-chat"

PROXY_VARS = [k for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy") if os.environ.get(k)]


def probe(label: str, client, *, trust_env: bool) -> None:
    headers = {
        "content-type": "application/json",
        "accept": "text/event-stream",
        "authorization": f"Bearer {sys.argv[1]}",
    }
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    started = time.time()
    try:
        with client.stream("POST", f"{BASE}/chat/completions", headers=headers, json=payload) as resp:
            status = resp.status_code
            lines = []
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    lines.append(line[:120])
                    if len(lines) >= 2:
                        break
        dt = time.time() - started
        if lines:
            print(f"[PASS] {label:42s} status={status} trust_env={trust_env} in {dt:.2f}s")
            for ln in lines:
                print(f"         {ln}")
        else:
            print(f"[FAIL] {label:42s} status={status} trust_env={trust_env} in {dt:.2f}s  (200 but zero data lines)")
    except Exception as exc:  # noqa: BLE001 - diagnostic must catch everything
        dt = time.time() - started
        print(f"[FAIL] {label:42s} trust_env={trust_env} in {dt:.2f}s  {type(exc).__name__}: {exc!r}"[:300])


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    print(f"endpoint={BASE} model={MODEL}")
    print(f"proxy env vars present: {PROXY_VARS if PROXY_VARS else '(none)'}")
    print()

    import httpx2

    probe("httpx2 (no proxy)", httpx2.Client(timeout=30, trust_env=False), trust_env=False)
    probe("httpx2 (trust_env default)", httpx2.Client(timeout=30, trust_env=True), trust_env=True)

    try:
        import httpx  # type: ignore[import-not-found]

        print("classic httpx available - testing")
        probe("classic httpx (no proxy)", httpx.Client(timeout=30, trust_env=False), trust_env=False)
        probe("classic httpx (trust_env default)", httpx.Client(timeout=30, trust_env=True), trust_env=True)
    except ImportError:
        print("classic httpx NOT installed - run with: uv run --with httpx --project driver python scripts/probe_deepseek.py <KEY>")


if __name__ == "__main__":
    main()
