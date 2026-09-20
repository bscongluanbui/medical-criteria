"""Run through docker compose exec dashboard python -m app.manage ..."""
import argparse
import getpass
import os
from sqlalchemy import delete, select
from app.auth import hash_password
from app.database import Account, WebSession, connect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create-user", "reset-password", "disable-user"])
    parser.add_argument("email")
    parser.add_argument("--role", choices=["admin", "reviewer"], default="admin")
    args = parser.parse_args()
    email = args.email.strip().lower()
    if "@" not in email or len(email) > 254:
        parser.error("email invalid")
    engine, sessions = connect(os.environ["DATABASE_URL"])
    try:
        with sessions.begin() as db:
            user = db.scalar(select(Account).where(Account.email == email).with_for_update())
            if args.action == "create-user" and user:
                parser.error("account already exists; use reset-password")
            if args.action != "create-user" and not user:
                parser.error("account not found")
            if args.action == "disable-user":
                user.active = False
            else:
                password = getpass.getpass("New password (12+ characters): ")
                if password != getpass.getpass("Confirm password: "):
                    parser.error("passwords differ")
                encoded = hash_password(password)
                if user:
                    user.password_hash = encoded
                else:
                    db.add(Account(email=email, password_hash=encoded, role=args.role, active=True))
            db.execute(delete(WebSession).where(WebSession.email == email))
        print("ACCOUNT_UPDATED: existing sessions revoked; password not displayed")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
