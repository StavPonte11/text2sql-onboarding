from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select
from pydantic import BaseModel
from app.db.engine import get_session
from app.models.models import Admin, AdminRead, AdminCreate
from app.services.auth import (
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_admin,
)

router = APIRouter(prefix="/admin", tags=["admin_auth"])

class Token(BaseModel):
    access_token: str
    token_type: str

@router.post("/login", response_model=Token)
def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session)
):
    admin = session.exec(select(Admin).where(Admin.username == form_data.username)).first()
    if not admin or not verify_password(form_data.password, admin.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = create_access_token(data={"sub": admin.username})
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=AdminRead)
def read_users_me(current_user: Admin = Depends(get_current_admin)):
    return current_user

@router.post("/register", response_model=AdminRead, status_code=status.HTTP_201_CREATED)
def create_admin(
    admin_in: AdminCreate,
    current_admin: Admin = Depends(get_current_admin),  # Only existing admins can create new admins
    session: Session = Depends(get_session)
):
    existing_admin = session.exec(select(Admin).where(Admin.username == admin_in.username)).first()
    if existing_admin:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_password = get_password_hash(admin_in.password)
    db_admin = Admin(username=admin_in.username, hashed_password=hashed_password)
    session.add(db_admin)
    session.commit()
    session.refresh(db_admin)
    return db_admin
