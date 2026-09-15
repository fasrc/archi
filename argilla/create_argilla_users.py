#!/usr/bin/env python3
"""Create Argilla users from a CSV roster.

A more flexible companion to `bootstrap_argilla.py --create-users`: supports a
per-user role and an optional explicit password column. Idempotent — existing
usernames are skipped.

Roster CSV (one user per line; '#' comments and blank lines ignored):
    username,Full Name,email[,role][,password]
- role     optional; default from --role (default: annotator).
           one of: owner | admin | annotator
- password optional; omit it. If omitted a secure random one is generated and
           printed once, which keeps the credential out of any file at all.

A roster holds real names and real email addresses, so keep it OUTSIDE this
repository — `argilla/*.csv` and `argilla/*.txt` are git-ignored precisely so a
filled-in one cannot be committed again. See argilla/argilla_users.csv.example
for the format.

Connection (same convention as the other argilla scripts):
    ARGILLA_API_URL   default http://localhost:3080
    ARGILLA_API_KEY   or ~/.archi/secrets/argilla_api_key.txt

Usage:
    export ARGILLA_API_KEY=$(cat ~/.archi/secrets/argilla_api_key.txt)
    python argilla/create_argilla_users.py ~/.archi/rosters/evaluators.csv
    python argilla/create_argilla_users.py ~/.archi/rosters/evaluators.csv \
        --workspace archi --role annotator
"""
import argparse
import os
import secrets
import sys
from pathlib import Path

VALID_ROLES = {"owner", "admin", "annotator"}


def client():
    import argilla as rg  # pyright: ignore[reportMissingImports]

    api_url = os.environ.get("ARGILLA_API_URL", "http://localhost:3080").rstrip("/")
    api_key = os.environ.get("ARGILLA_API_KEY")
    if not api_key:
        key_path = Path.home() / ".archi" / "secrets" / "argilla_api_key.txt"
        if key_path.exists():
            api_key = key_path.read_text().strip()
    if not api_key:
        sys.exit("ERROR: set ARGILLA_API_KEY or place ~/.archi/secrets/argilla_api_key.txt")
    return rg.Argilla(api_url=api_url, api_key=api_key)


def main() -> int:
    import argilla as rg  # pyright: ignore[reportMissingImports]

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roster", type=Path, help="CSV roster path")
    ap.add_argument("--workspace", default="archi", help="workspace to add users to (default: archi)")
    ap.add_argument("--role", default="annotator", choices=sorted(VALID_ROLES),
                    help="default role when a row omits one (default: annotator)")
    args = ap.parse_args()

    if not args.roster.exists():
        sys.exit(f"ERROR: roster not found: {args.roster}")

    rg_client = client()
    ws = rg_client.workspaces(name=args.workspace)
    if ws is None:
        sys.exit(f"ERROR: workspace '{args.workspace}' does not exist. Create it first "
                 f"(e.g. python scripts/bootstrap_argilla.py --create-workspace).")

    created, skipped = [], []
    for lineno, raw in enumerate(args.roster.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = [f.strip() for f in line.split(",")]
        username = fields[0]
        if not username:
            print(f"line {lineno}: blank username, skipping", file=sys.stderr)
            continue
        first_name = fields[1] if len(fields) > 1 and fields[1] else username
        role = (fields[3] if len(fields) > 3 and fields[3] else args.role).lower()
        if role not in VALID_ROLES:
            sys.exit(f"line {lineno}: invalid role '{role}' (one of {sorted(VALID_ROLES)})")
        password = fields[4] if len(fields) > 4 and fields[4] else secrets.token_urlsafe(16)

        if rg_client.users(username=username) is not None:
            skipped.append(username)
            print(f"User '{username}' already exists; skipping.")
            continue

        user = rg.User(username=username, first_name=first_name, role=role, password=password)
        user.create()
        ws.add_user(user)
        created.append((username, role, password))
        print(f"Created user: {username} (role={role})")

    if created:
        print("\n=== TEMPORARY PASSWORDS — distribute securely, then ask each user to rotate ===")
        for username, role, password in created:
            print(f"  {username} [{role}]: {password}")
    print(f"\nDone. created={len(created)} skipped={len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
