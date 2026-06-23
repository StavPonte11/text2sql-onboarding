from core.db.engine import engine
from core.models.models import AuthConfigRead, SecurityUserRead
from fastapi import APIRouter, Depends, Request
from python_core_utils.auth import create_sso_router
from sqlmodel import Session

from app.config import auth_settings
from app.core.auth import get_current_user, sync_user_from_payload

router = APIRouter()

async def on_login_success(request: Request, userinfo: dict, provider: str):
    # Synchronize user details with the database
    with Session(engine) as db:
        user = sync_user_from_payload(db, userinfo, provider)
        request.session["user_id"] = user.id

# Create the SSO router from core-utils
sso_router = create_sso_router(auth_settings, on_login_success=on_login_success)
router.include_router(sso_router)

@router.get("/config", response_model=AuthConfigRead)
def get_auth_config():
    return {
        "ENABLE_KEYCLOAK": auth_settings.ENABLE_KEYCLOAK,
        "ENABLE_GOOGLE": auth_settings.ENABLE_GOOGLE,
    }

@router.get("/me", response_model=SecurityUserRead)
def get_me(user = Depends(get_current_user)):
    return user

