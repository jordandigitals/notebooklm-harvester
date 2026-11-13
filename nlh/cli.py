"""Command line front end (used for the live checks; the web UI arrives in M3)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nlh import adapter, harvest, media, paths
from nlh.ledger import Ledger


def _account(ledger: Ledger, profile: str):
    """Look up a registered account or exit with a plain message."""
    acct = ledger.account_by_profile(profile)
    if not acct:
        sys.exit(f"No account '{profile}'. Add it first (nlh adopt).")
    return acct


def cmd_adopt(ledger: Ledger, a) -> int:
    """Register an existing library profile (for example the P0 session) as an account."""
    if not paths.storage_path(a.profile).exists():
        sys.exit(f"No saved session for profile '{a.profile}' in the app home.")
    if ledger.account_by_profile(a.profile):
        print("Already registered.")
        return 0
    ledger.add_account(a.profile, a.label or a.profile, a.email, a.chrome_profile)
    paths.secure_session_file(a.profile)
    print(f"Registered account '{a.profile}'.")
    return 0


def cmd_accounts(ledger: Ledger, a) -> int:
    """List accounts and, with --check, their session status."""
    for acct in ledger.accounts():
        line = f"{acct.profile_name}  label={acct.label}  out={acct.output_dir or '-'}"
        if a.check:
            st = adapter.check_session(acct.profile_name)
            ledger.update_account(acct.id, last_status=st.state, last_checked=_now())
            line += f"  status={st.state}"
        print(line)
    return 0


def _now() -> str:
    from nlh.ledger import now

    return now()


def cmd_list(ledger: Ledger, a) -> int:
    """List notebooks and audio overview status for an account."""
    acct = _account(ledger, a.account)
    lib = adapter.list_library(acct.profile_name)
    total = 0
    for nb, ovs in lib:
        print(f"{nb.id[:8]}  {nb.title}  ({len(ovs)} audio)")
        for ov in ovs:
            total += 1
            st = harvest.overview_status(ledger, ov)
            print(f"    {ov.artifact_id[:8]}  {st:<18} {ov.title}")
    print(f"{len(lib)} notebooks, {total} audio overviews")
    return 0


def cmd_harvest(ledger: Ledger, a) -> int:
    """Harvest selected artifact ids, or all new overviews, into the output folder."""
    acct = _account(ledger, a.account)
    out = Path(a.out).expanduser() if a.out else (Path(acct.output_dir) if acct.output_dir else None)
    if not out:
        sys.exit("Choose an output folder with --out.")
    out.mkdir(parents=True, exist_ok=True)
    if a.out:
        ledger.update_account(acct.id, output_dir=str(out))
    lib = adapter.list_library(acct.profile_name)
    ovs = [ov for _nb, items in lib for ov in items]
    if a.artifact:
        wanted = tuple(a.artifact)
        ovs = [ov for ov in ovs if ov.artifact_id.startswith(wanted)]
    else:
        ovs = [ov for ov in ovs if harvest.overview_status(ledger, ov) == harvest.NEW]
    if not ovs:
        print("Nothing new to harvest.")
        return 0

    def show(i, n, ov, msg):
        print(f"[{i + 1}/{n}] {ov.title}: {msg}")

    results = harvest.harvest_many(ledger, acct, ovs, out, on_item=show)
    saved = sum(r.outcome == "saved" for r in results)
    print(
        f"Saved {saved}, skipped {sum(r.outcome == 'skipped' for r in results)}, "
        f"failed {sum(r.outcome == 'failed' for r in results)}"
    )
    return 0 if all(r.outcome != "failed" for r in results) else 1


def cmd_ledger(ledger: Ledger, a) -> int:
    """Show the ledger rows."""
    for h in ledger.harvests():
        print(
            f"{h.saved_at}  {h.artifact_id[:8]}  {h.duration_s:.2f}s  {h.size_bytes}B  "
            f"present={h.file_present}  {h.saved_path}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for `python -m nlh`."""
    p = argparse.ArgumentParser(prog="nlh", description="NotebookLM Harvester")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("adopt", help="register an existing session profile")
    s.add_argument("profile")
    s.add_argument("--label")
    s.add_argument("--email")
    s.add_argument("--chrome-profile")
    s = sub.add_parser("accounts")
    s.add_argument("--check", action="store_true")
    s = sub.add_parser("list")
    s.add_argument("--account", required=True)
    s = sub.add_parser("harvest")
    s.add_argument("--account", required=True)
    s.add_argument("--out")
    s.add_argument("--artifact", action="append", help="artifact id or prefix (repeatable)")
    sub.add_parser("ledger")
    sub.add_parser("serve", help="start the local web page")
    a = p.parse_args(argv)

    missing = [k for k, ok in media.tools_available().items() if not ok]
    if missing and a.cmd in ("harvest", "serve"):
        sys.exit(f"Missing {', '.join(missing)}. See INSTALL.md.")
    if a.cmd == "serve":
        from nlh.server import serve

        return serve()
    ledger = Ledger()
    try:
        return {
            "adopt": cmd_adopt,
            "accounts": cmd_accounts,
            "list": cmd_list,
            "harvest": cmd_harvest,
            "ledger": cmd_ledger,
        }[a.cmd](ledger, a)
    finally:
        ledger.close()
