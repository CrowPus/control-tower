"""Admin accounts from the command line — the only way to create the first one.

    python -m app.admin_cli create-owner --email you@example.com --name "Your Name"

Prints a one-time link (valid 72 hours) where the owner sets their password;
their first sign-in then sets up the authenticator app. Nothing here can create
an admin without that link being followed.

    .venv/bin/python -m app.admin_cli load-employer-documents   (in the admin container: it needs the admin database role)

Adds the employer contract texts in app/legal/employer as drafts (version
2026-10-01). An admin owner reads and approves them in Admin → Commercial; a
version already there is left alone.

    .venv/bin/python -m app.admin_cli load-routed-terms   (the same, for the routed slot terms; German, like every contract text)
"""

from __future__ import annotations

import argparse

from .admin import auth
from .db import admin_transaction
from .services import system_mail


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.admin_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-owner", help="invite an owner admin and print the set-up link")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    if args.command == "create-owner":
        from email_validator import EmailNotValidError, validate_email

        try:
            # The same check the sign-in form applies; an address it refuses could never sign in.
            args.email = validate_email(args.email, check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise SystemExit(f"Not a usable e-mail address: {exc}") from None
        with admin_transaction(write=True) as session:
            admin, raw = auth.invite(session, email=args.email, full_name=args.name, role="owner", invited_by=None)
            auth.record(session, None, "admin.owner_created_from_cli", target_type="admin", target_id=admin.id, metadata={"email": admin.email})
            email = admin.email
        link = auth.invite_link(raw)
        print(f"Owner invited: {email}")
        try:
            system_mail.send("admin_invitation", to=email, name=args.name, locale="en", link=link)
            print("The set-up link was e-mailed to that address.")
        except Exception as exc:  # noqa: BLE001 - the printed link still works
            print(f"Could not e-mail the link ({exc.__class__.__name__}).")
        print(f"Set the password here (valid 72 hours): {link}")


if __name__ == "__main__":
    main()
