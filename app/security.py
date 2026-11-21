import base64
import hashlib
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from cryptography.fernet import Fernet
from itsdangerous import URLSafeTimedSerializer, BadSignature
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from .config import settings
from .db import get_db
from .models import User

hasher = PasswordHasher()


def verify_password(password, hashed):
    try:
        return hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def cipher():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.secret.encode()).digest()))


def serializer():
    return URLSafeTimedSerializer(settings.secret, salt='spa-session-v1')


def current_user(request: Request, db: Session = Depends(get_db)):
    try:
        uid = serializer().loads(request.cookies.get('spa_session', ''), max_age=86400)
    except BadSignature:
        raise HTTPException(401, '请先登录')
    user = db.get(User, uid)
    if user is None or not user.approved:
        raise HTTPException(401, '账号未获批准')
    return user


def admin_user(user: User = Depends(current_user)):
    if not user.is_admin:
        raise HTTPException(403, '需要管理员权限')
    return user
