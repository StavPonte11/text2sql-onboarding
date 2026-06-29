from core.db.engine import get_session
from core.models.models import SecurityUser
from fastapi import Depends, HTTPException, status
from sqlmodel import Session, select


def get_user_by_email(email: str, session: Session) -> SecurityUser | None:
    """
    Find a security user by email.
    
    Parameters:
    	email (str): The email address to match.
    	session (Session): Database session used to query users.
    
    Returns:
    	SecurityUser | None: The matching user, or None if no user has the given email.
    """
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
