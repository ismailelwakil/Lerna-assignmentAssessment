"""Authentication & registration (instructor/student). No auto-created users."""
from __future__ import annotations

from ..db import db
from ..exceptions import AuthError, ForbiddenError, ValidationError
from ..models import AuditEvent, User
from ..security import (create_session_token, decode_session_token, hash_password,
                        new_id, validate_username, verify_password)


def register(username: str, password: str, role: str, display_name: str = "") -> dict:
    if role not in {"instructor", "student"}:
        raise ValidationError("Role must be instructor or student.")
    username = validate_username(username)
    with db().session_scope() as session:
        if session.query(User).filter(User.username == username).first():
            raise ValidationError(f"Username “{username}” is already taken.")
        user = User(id=new_id(), username=username,
                    password_hash=hash_password(password), role=role,
                    display_name=(display_name or username)[:120])
        session.add(user)
        session.add(AuditEvent(actor_id=user.id, event_type="user_registered",
                               entity_type="user", entity_id=user.id,
                               detail={"role": role}))
        uid, name = user.id, user.display_name
    return {"user_id": uid, "username": username, "role": role,
            "display_name": name, "token": create_session_token(uid, role)}


def login(username: str, password: str) -> dict:
    with db().session_scope() as session:
        user = session.query(User).filter(User.username == (username or "").strip()).first()
        if not user or not verify_password(password or "", user.password_hash):
            raise AuthError("Invalid username or password.")
        return {"user_id": user.id, "username": user.username, "role": user.role,
                "display_name": user.display_name,
                "token": create_session_token(user.id, user.role)}


def user_from_token(token: str) -> dict:
    payload = decode_session_token(token)
    with db().session_scope() as session:
        user = session.get(User, payload["sub"])
        if not user:
            raise AuthError("Account no longer exists.")
        return {"user_id": user.id, "username": user.username,
                "role": user.role, "display_name": user.display_name}


def require_role(user: dict, *roles: str) -> None:
    if user["role"] not in roles:
        raise ForbiddenError("This action requires a different role.")


def get_user(user_id: str) -> User | None:
    with db().session_scope() as session:
        return session.get(User, user_id)
