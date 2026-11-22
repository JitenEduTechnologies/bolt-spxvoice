"""
Secure Local Administrator Password Reset Utility
SPX Voice - Self-Hosted Single-Tenant Local Deployment

Safety & Verification Rules:
- Require User ID 1, email admin@spxvoice.com, and is_superuser == True to match.
- Interactive masked input via getpass (no terminal echo).
- Application's native bcrypt password hashing via api.utils.auth.hash_password.
- Verify update_res.rowcount == 1 BEFORE committing; roll back transaction immediately on any unexpected result.
- Verify the newly committed hash validates against the input password using api.utils.auth.verify_password.
- No plain passwords logged, printed, or saved.
- Preserves all other accounts, organizations, workflows, campaigns, and audio recordings.
"""

import asyncio
import getpass
import sys


TARGET_USER_ID = 1
TARGET_EMAIL = "admin@spxvoice.com"


async def run_reset():
    from sqlalchemy import select, update
    from api.db import db_client
    from api.db.models import UserModel
    from api.utils.auth import hash_password, verify_password

    print("=====================================================")
    print(" SPX Voice — Local Administrator Password Recovery   ")
    print("=====================================================")

    # 1. Require User ID 1, email admin@spxvoice.com, and superuser status to match
    async with db_client.async_session() as session:
        stmt = (
            select(UserModel)
            .where(
                UserModel.id == TARGET_USER_ID,
                UserModel.email == TARGET_EMAIL,
                UserModel.is_superuser.is_(True),
            )
        )
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

        if not user:
            print(f"[Error] Target administrator account (ID: {TARGET_USER_ID}, email: {TARGET_EMAIL}, superuser: True) not found.")
            sys.exit(1)

        print(f"[Account Verified] Target: {user.email} (ID: {user.id}, Superuser: {user.is_superuser}, Org ID: {user.selected_organization_id})")

    # 2. Interactive masked prompt (no terminal echo)
    try:
        new_pwd = getpass.getpass("\nEnter new administrator password (min 8 characters): ")
        if not new_pwd or len(new_pwd.strip()) < 8:
            print("[Error] Password must be at least 8 characters long.")
            sys.exit(1)

        confirm_pwd = getpass.getpass("Confirm new administrator password: ")
        if new_pwd != confirm_pwd:
            print("[Error] Passwords do not match. Aborting without database changes.")
            sys.exit(1)
        del confirm_pwd
    except (KeyboardInterrupt, EOFError):
        print("\n[Aborted] Operation canceled by user.")
        sys.exit(1)

    # 3. Hash password using application's native bcrypt utility
    new_hash = hash_password(new_pwd)

    # 4. Atomic database update with explicit rowcount check BEFORE commit
    async with db_client.async_session() as session:
        update_stmt = (
            update(UserModel)
            .where(
                UserModel.id == TARGET_USER_ID,
                UserModel.email == TARGET_EMAIL,
                UserModel.is_superuser.is_(True),
            )
            .values(password_hash=new_hash)
        )
        update_res = await session.execute(update_stmt)

        # STRICT SAFETY: Verify rowcount == 1 BEFORE commit. Roll back on any unexpected count.
        if update_res.rowcount != 1:
            await session.rollback()
            print(f"[CRITICAL ERROR] Update affected {update_res.rowcount} rows (expected exactly 1). Transaction rolled back without changes.")
            sys.exit(1)

        await session.commit()

    # 5. Post-commit verification using application's native verify_password function
    async with db_client.async_session() as session:
        check_stmt = (
            select(UserModel)
            .where(
                UserModel.id == TARGET_USER_ID,
                UserModel.email == TARGET_EMAIL,
                UserModel.is_superuser.is_(True),
            )
        )
        check_res = await session.execute(check_stmt)
        verified_user = check_res.scalar_one_or_none()

        if not verified_user or not verified_user.password_hash:
            print("[Error] Failed to reload administrator record from database after update.")
            sys.exit(1)

        # Verify hash match with application's bcrypt verification function
        if not verify_password(new_pwd, verified_user.password_hash):
            print("[Error] Post-update password verification failed: hash does not validate against supplied password.")
            sys.exit(1)

    # Unbind local reference
    del new_pwd

    print("\n-----------------------------------------------------")
    print(f"[SUCCESS] Exactly 1 authorized administrator record was updated.")
    print(f"[SUCCESS] Account: {verified_user.email} (User ID: {verified_user.id}).")
    print(f"[SUCCESS] Native bcrypt hash verification passed.")
    print(f"[SUCCESS] Zero duplicate records created; all organizations, leads, and workflows preserved.")
    print("-----------------------------------------------------")
    print("\nYou can now log in at:")
    print("  http://localhost:3020/auth/login")
    print(f"  Email: {TARGET_EMAIL}\n")


if __name__ == "__main__":
    asyncio.run(run_reset())
