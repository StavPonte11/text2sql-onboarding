from fastapi import Depends, HTTPException, status
from sqlmodel import Session, select

from app.db.engine import get_session
from app.models.models import SecurityUser


def get_user_by_email(email: str, session: Session) -> SecurityUser | None:
    """Fetch a user from security.users by email."""
    return session.exec(select(SecurityUser).where(SecurityUser.email == email)).first()


def require_admin(email: str, session: Session = Depends(get_session)) -> SecurityUser:
    """
    Dependency that validates the email belongs to an active admin user.
    Raises 403 if the user is not found, inactive, or not an admin.
    """
    user = get_user_by_email(email, session)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not found or inactive",
        )
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have admin permissions",
        )
    return user
