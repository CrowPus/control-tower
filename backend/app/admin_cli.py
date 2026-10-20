"""Admin accounts from the command line — the only way to create the first one.

    python -m app.admin_cli create-owner --email you@example.com --name "Your Name"

Prints a one-time link (valid 72 hours) where the owner sets their password;
their first sign-in then sets up the authenticator app. Nothing here can create
an admin without that link being followed.

    python -m app.admin_cli invite-again --email you@example.com

A new set-up link for an admin who has not set a password yet (the old link stops working): for a link that was
lost, mistyped or expired.

    python -m app.admin_cli has-owner

Exits 0 when an owner admin exists (the installer asks before inviting another), 1 when none does.
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
    sub.add_parser("has-owner", help="exit 0 when an owner admin exists")
    again = sub.add_parser("invite-again", help="a new set-up link for an admin who has not set a password yet")
    again.add_argument("--email", required=True)
    args = parser.parse_args(argv)
    if args.command == "has-owner":
        from sqlalchemy import select

        from .models import AdminUser

        with admin_transaction() as session:
            found = session.execute(select(AdminUser.id).where(AdminUser.role == "owner").limit(1)).first()
        raise SystemExit(0 if found else 1)
    if args.command == "invite-again":
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import select

        from .core.passwords import new_token, token_digest
        from .models import AdminUser

        with admin_transaction(write=True) as session:
            admin = session.execute(select(AdminUser).where(AdminUser.email == args.email.strip().lower())).scalar_one_or_none()
            if admin is None or admin.status != "invited":
                raise SystemExit("No admin with that e-mail is waiting to set a password. (Signed in already? Another owner can manage admins in Admin → Admins.)")
            raw = new_token()
            admin.invite_token_hash = token_digest(raw)
            admin.invite_expires_at = datetime.now(UTC) + timedelta(hours=auth.INVITE_HOURS)
            auth.record(session, None, "admin.invite_renewed_from_cli", target_type="admin", target_id=admin.id, metadata={"email": admin.email})
        print(f"Set the password here (valid {auth.INVITE_HOURS} hours; the earlier link no longer works): {auth.invite_link(raw)}")
        return
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
            if system_mail.is_configured():
                print(f"Could not e-mail the link ({exc.__class__.__name__}); use the link below.")
            else:
                print("No mailbox is set up for the install yet (SMTP_* in .env), so the link is not e-mailed: use it below.")
        print(f"Set the password here (valid 72 hours): {link}")


if __name__ == "__main__":
    main()
