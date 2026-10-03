"""
Reset an admin user's password when it has been forgotten.

Passwords are stored as bcrypt hashes, so an existing password can't be
recovered -- only replaced. The new password is typed at a hidden prompt
and never echoed or logged. Run from backend/ (uses DATABASE_URL in .env):

    python reset_admin_password.py                 # lists admins, asks which
    python reset_admin_password.py admin@example.com
"""

import sys
from getpass import getpass

from app.database import SessionLocal
from app.auth.security import hash_password
from app.models.user import User, UserRole


def main() -> int:
    db = SessionLocal()
    try:
        admins = db.query(User).filter(User.role == UserRole.ADMIN.value).all()
        if not admins:
            print("No admin users found in this database.")
            return 1

        email = sys.argv[1] if len(sys.argv) > 1 else None
        if not email:
            print("Admin users:")
            for a in admins:
                print(f"  {a.email}{'' if a.is_active else '  (inactive)'}")
            email = input("E-mail of the admin to reset: ").strip()

        user = next((a for a in admins if a.email.lower() == email.lower()), None)
        if user is None:
            print(f"No admin user with e-mail {email!r}.")
            return 1

        password = getpass("New password (min 8 characters): ")
        if len(password) < 8:
            print("Password must be at least 8 characters. Nothing changed.")
            return 1
        if getpass("Repeat new password: ") != password:
            print("Passwords do not match. Nothing changed.")
            return 1

        user.hashed_password = hash_password(password)
        user.is_active = True
        db.commit()
        print(f"Password reset for {user.email}. You can now log in.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
