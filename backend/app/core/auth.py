from core.db.engine import get_session
from core.models.models import Organization, SecurityUser
from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session, select


def sync_user_from_payload(db: Session, payload: dict, provider: str) -> SecurityUser:
    email = payload.get("email")
    sub = payload.get("sub")
    preferred_name = payload.get("preferred_username") or payload.get("name")
    name = preferred_name or (email.split("@")[0] if email else sub)

    sso_id = sub

    if not email and not sso_id:
        raise ValueError("Invalid user payload")

    user = None
    if sso_id:
        user = db.exec(select(SecurityUser).where(SecurityUser.sso_id == sso_id)).first()

    if not user and email:
        user = db.exec(select(SecurityUser).where(SecurityUser.email == email)).first()

    if not user and not email:
        # current implementation: allow invalid email in such cases, but we might want to change that
        email = f"{sso_id}@{provider}.sso"

    if not user:
        user = SecurityUser(
            email=email,
            name=name,
            sso_id=sso_id,
            provider=provider
        )
        db.add(user)
    else:
        user.name = name
        user.sso_id = sso_id
        user.provider = provider
        db.add(user)

    # Handle SSO groups mapping
    groups = payload.get("groups", [])
    if groups:
        cleaned_groups = [g.lstrip('/') for g in groups]
        user_org_ids = {org.id for org in user.organizations}

        for group_name in cleaned_groups:
            org = db.exec(select(Organization).where(Organization.name == group_name)).first()
            if not org:
                org = Organization(name=group_name)
                db.add(org)
                db.flush() # flush to get id

            if org.id not in user_org_ids:
                user.organizations.append(org)
                user_org_ids.add(org.id)

        user.organizations = [org for org in user.organizations if org.name in cleaned_groups]

    db.commit()
    db.refresh(user)
    return user

def get_current_user(request: Request, db: Session = Depends(get_session)) -> SecurityUser:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    user = db.get(SecurityUser, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return user

def check_admin(current_user: SecurityUser = Depends(get_current_user)) -> SecurityUser:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative privileges required"
        )
    return current_user
