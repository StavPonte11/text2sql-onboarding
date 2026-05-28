from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

from app.db.engine import get_session
from app.models.models import SecurityUserRead
from app.services.auth import get_user_by_email

router = APIRouter(prefix="/admin", tags=["admin_auth"])


class LoginRequest(BaseModel):
    email: str


@router.post("/login", response_model=SecurityUserRead)
def login(
    body: LoginRequest,
    session: Session = Depends(get_session),
):
    """
    Validate that the supplied email belongs to an active admin in security.users.
    Returns the user record on success; raises 403 when the user is not an admin.
    """
    user = get_user_by_email(body.email, session)

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not found or inactive",
        )

    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access the admin panel",
        )

    return user
