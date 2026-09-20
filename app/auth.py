"""Password authentication and revocable, hashed, database-backed sessions."""
import hashlib
import secrets
import time
from sqlalchemy import delete, func, select
from fastapi import HTTPException
from app.database import Account, LoginAttempt, WebSession


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password):
    if not 12 <= len(password) <= 128:
        raise ValueError("Mật khẩu cần từ 12 đến 128 ký tự.")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000).hex()
    return f"pbkdf2_sha256$600000${salt}${digest}"


def verify_password(password, encoded):
    method, iterations, salt, digest = encoded.split("$")
    result = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return method == "pbkdf2_sha256" and secrets.compare_digest(result, digest)


DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


class Auth:
    def __init__(self, sessions):
        self.sessions = sessions

    def login(self, email, password):
        current = int(time.time())
        identity = token_hash(email)
        # Persist attempts before password verification; no transaction rollback on a bad password.
        with self.sessions.begin() as db:
            if db.bind.dialect.name == "postgresql":
                from sqlalchemy import text
                db.execute(text("SELECT pg_advisory_xact_lock(2026092002)"))
            db.execute(delete(LoginAttempt).where(LoginAttempt.timestamp < current - 900))
            own = db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.identity == identity))
            total = db.scalar(select(func.count()).select_from(LoginAttempt))
            if own >= 5 or total >= 100:
                raise HTTPException(429, "Quá nhiều lần đăng nhập. Vui lòng thử lại sau 15 phút.")
            db.add(LoginAttempt(identity=identity, timestamp=current))
            account = db.get(Account, email)
            encoded = account.password_hash if account else DUMMY_HASH
            active = account is not None and account.active
        valid = verify_password(password, encoded)
        if not valid or not active:
            raise HTTPException(401, "Email hoặc mật khẩu không đúng.")
        token, csrf = secrets.token_urlsafe(48), secrets.token_hex(32)
        with self.sessions.begin() as db:
            current_account = db.scalar(select(Account).where(Account.email == email).with_for_update())
            if not current_account or not current_account.active or current_account.password_hash != encoded:
                raise HTTPException(401, "Thông tin tài khoản đã thay đổi. Vui lòng đăng nhập lại.")
            db.execute(delete(WebSession).where(WebSession.expires <= current))
            db.execute(delete(LoginAttempt).where(LoginAttempt.identity == identity))
            db.add(WebSession(token_hash=token_hash(token), email=email, csrf=csrf, expires=current + 8 * 3600))
        return token

    def resolve(self, token):
        if not token or len(token) > 200:
            raise HTTPException(401, "Vui lòng đăng nhập.")
        with self.sessions() as db:
            session = db.get(WebSession, token_hash(token))
            if session is None or session.expires <= int(time.time()):
                raise HTTPException(401, "Phiên đăng nhập đã hết hạn.")
            account = db.get(Account, session.email)
            if account is None or not account.active:
                raise HTTPException(401, "Vui lòng đăng nhập lại.")
            return {"email": account.email, "role": account.role, "csrf": session.csrf}

    def logout(self, token):
        with self.sessions.begin() as db:
            db.execute(delete(WebSession).where(WebSession.token_hash == token_hash(token)))
