#!/usr/bin/env python3
"""Three-way deployment drift check for the LLM Usage Tracker.

Compares the commit SHA across three layers and fails if they disagree:

    expected  = origin/main HEAD           (git ls-remote)
    deployed  = Render's live deploy        (Render API)
    running   = the live app's GET /version (HTTP)

We observed that pushing `main` does NOT auto-deploy this Render service, so
"expected != deployed" (pushed but not deployed) is a real, recurring risk;
"deployed != running" catches a stale/failed instance serving an old image.

Stdlib only — no third-party packages, no monitoring infrastructure. Exits 0
only when every comparison that can be made agrees; any mismatch, or any value
that should be present but can't be resolved, exits non-zero.

Config (env var, or flag override):
    RENDER_API_KEY      Bearer token for the Render API (required unless
                        --skip-deployed). Never printed.
    RENDER_SERVICE_ID   default: srv-daegp0tbedkc73d7fcbg
    APP_URL             default: https://llm-usage-tracker-97q0.onrender.com
    GIT_REMOTE          default: origin
    GIT_BRANCH          default: main

Examples:
    python scripts/check_drift.py                       # full three-way check
    RENDER_API_KEY=... python scripts/check_drift.py
    python scripts/check_drift.py --skip-deployed --skip-running  # expected only
    python scripts/check_drift.py --self-test           # offline logic check
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_SERVICE_ID = "srv-daegp0tbedkc73d7fcbg"
DEFAULT_APP_URL = "https://llm-usage-tracker-97q0.onrender.com"
RENDER_API = "https://api.render.com/v1"
_HTTP_TIMEOUT = 15


# ---- readers (each returns a SHA string, or None if it can't be resolved) ----
def expected_commit(remote: str, branch: str) -> str | None:
    """origin/main HEAD via `git ls-remote` (read-only; no local checkout state)."""
    try:
        out = subprocess.run(
            ["git", "ls-remote", remote, f"refs/heads/{branch}"],
            capture_output=True, text=True, timeout=_HTTP_TIMEOUT, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"  ! could not read {remote}/{branch}: {exc}", file=sys.stderr)
        return None
    if not out:
        print(f"  ! {remote}/{branch} not found", file=sys.stderr)
        return None
    return out.split()[0].strip()


def _http_json(url: str, headers: dict | None = None):
    # urlopen of an https URL we control (the Render API or our own app host).
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def deployed_commit(api_key: str, service_id: str) -> str | None:
    """The commit of the service's current LIVE deploy, via the Render API."""
    if not api_key:
        print("  ! RENDER_API_KEY not set - cannot read the deployed commit", file=sys.stderr)
        return None
    url = f"{RENDER_API}/services/{service_id}/deploys?limit=20"
    try:
        data = _http_json(url, {"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        print(f"  ! Render API request failed: {exc}", file=sys.stderr)
        return None
    for item in data if isinstance(data, list) else []:
        dep = item.get("deploy", item) if isinstance(item, dict) else {}
        if dep.get("status") == "live":
            return ((dep.get("commit") or {}).get("id") or "").strip() or None
    print("  ! no live deploy found in the Render API response", file=sys.stderr)
    return None


def running_commit(app_url: str) -> str | None:
    """The commit the live app reports at GET /version (null off-Render)."""
    try:
        data = _http_json(app_url.rstrip("/") + "/version")
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        print(f"  ! GET {app_url}/version failed: {exc}", file=sys.stderr)
        return None
    return (data.get("commit") or None) if isinstance(data, dict) else None


# ---- pure comparison logic (unit-tested) ----
def _sha_eq(a: str | None, b: str | None) -> bool:
    """True if two SHAs refer to the same commit (case-insensitive; tolerates one
    being an abbreviation of the other, min 7 chars)."""
    if not a or not b:
        return False
    a, b = a.lower(), b.lower()
    if a == b:
        return True
    lo, hi = sorted((a, b), key=len)
    return len(lo) >= 7 and hi.startswith(lo)


def compare(expected, deployed, running, *, skip_deployed=False, skip_running=False):
    """Pure: returns (in_sync, report_lines). in_sync is True only when every
    comparison that can be made agrees. A value that is None but not skipped is a
    failure (can't confirm sync). If no comparison can be made at all, that's
    reported but treated as in_sync (nothing to contradict)."""
    lines: list[str] = []
    ok = True
    comparisons = 0

    def pair(label: str, left, right, left_skipped, right_skipped):
        nonlocal ok, comparisons
        if left_skipped or right_skipped:
            lines.append(f"  {label:<22} SKIPPED")
            return
        comparisons += 1
        if left is None or right is None:
            ok = False
            lines.append(f"  {label:<22} UNKNOWN (a value could not be resolved)")
        elif _sha_eq(left, right):
            lines.append(f"  {label:<22} MATCH")
        else:
            ok = False
            lines.append(f"  {label:<22} DRIFT")

    pair("expected vs deployed:", expected, deployed, False, skip_deployed)
    pair("deployed vs running:", deployed, running, skip_deployed, skip_running)

    if comparisons == 0:
        lines.append("  (no comparisons could be made - nothing to contradict)")
    return ok, lines


def _fmt(label: str, sha, skipped: bool) -> str:
    if skipped:
        return f"  {label:<30} (skipped)"
    return f"  {label:<30} {sha if sha else '<unresolved>'}"


def _self_test() -> int:
    long = "a" * 40
    other = "b" * 40
    cases = [
        ((long, long, long, False, False), True),          # all agree
        ((long, other, long, False, False), False),        # pushed, not deployed
        ((long, long, other, False, False), False),        # stale running instance
        ((long, None, long, False, False), False),         # deployed unresolved
        ((long, None, None, True, True), True),            # both skipped -> nothing to contradict
        ((long, long[:12], long, False, False), True),     # abbreviation matches
    ]
    failures = 0
    for args, want in cases:
        got, _ = compare(*args[:3], skip_deployed=args[3], skip_running=args[4])
        if got != want:
            failures += 1
            print(f"self-test FAIL: compare{args} -> {got}, want {want}", file=sys.stderr)
    print("self-test: PASS" if not failures else f"self-test: {failures} FAIL", file=sys.stderr)
    return 1 if failures else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Three-way deployment drift check.")
    p.add_argument("--service-id", default=os.environ.get("RENDER_SERVICE_ID", DEFAULT_SERVICE_ID))
    p.add_argument("--app-url", default=os.environ.get("APP_URL", DEFAULT_APP_URL))
    p.add_argument("--git-remote", default=os.environ.get("GIT_REMOTE", "origin"))
    p.add_argument("--git-branch", default=os.environ.get("GIT_BRANCH", "main"))
    p.add_argument("--skip-deployed", action="store_true", help="don't query the Render API")
    p.add_argument("--skip-running", action="store_true", help="don't query the live app")
    p.add_argument("--self-test", action="store_true", help="run the offline comparison-logic check and exit")
    args = p.parse_args(argv)

    if args.self_test:
        return _self_test()

    expected = expected_commit(args.git_remote, args.git_branch)
    deployed = None if args.skip_deployed else deployed_commit(
        os.environ.get("RENDER_API_KEY", ""), args.service_id
    )
    running = None if args.skip_running else running_commit(args.app_url)

    print("Deployment drift check - llm-usage-tracker")
    print(_fmt(f"expected ({args.git_remote}/{args.git_branch}):", expected, False))
    print(_fmt("deployed (Render live):", deployed, args.skip_deployed))
    print(_fmt("running  (GET /version):", running, args.skip_running))
    print()

    in_sync, lines = compare(
        expected, deployed, running,
        skip_deployed=args.skip_deployed, skip_running=args.skip_running,
    )
    for line in lines:
        print(line)
    print()
    print(f"  overall: {'IN SYNC' if in_sync else 'DRIFT'}")
    return 0 if in_sync else 1


if __name__ == "__main__":
    raise SystemExit(main())
