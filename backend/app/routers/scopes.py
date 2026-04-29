from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.db.engine import get_session
from app.models.models import UserScope, UserScopeCreate, UserScopeRead

router = APIRouter(prefix="/scopes", tags=["scopes"])


@router.get("", response_model=list[UserScopeRead])
def list_scopes(session: Session = Depends(get_session)):
    return session.exec(select(UserScope)).all()


@router.post("", response_model=UserScopeRead, status_code=201)
def create_scope(payload: UserScopeCreate, session: Session = Depends(get_session)):
    scope = UserScope(**payload.model_dump())
    session.add(scope)
    session.commit()
    session.refresh(scope)
    return scope


@router.post("/{scope_id}/activate", response_model=UserScopeRead)
def activate_scope(scope_id: str, session: Session = Depends(get_session)):
    scope = session.get(UserScope, scope_id)
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")

    # Deactivate all scopes for this user
    all_scopes = session.exec(
        select(UserScope).where(UserScope.user_id == scope.user_id)
    ).all()
    for s in all_scopes:
        s.is_active = False
        session.add(s)

    scope.is_active = True
    session.add(scope)
    session.commit()
    session.refresh(scope)
    return scope
