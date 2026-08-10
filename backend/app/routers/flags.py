"""
Feature Flags & Execution Modes API (G4-01)
============================================
Endpoints:
  GET  /flags/                   - list all flags
  GET  /flags/{name}             - get single flag
  PATCH /flags/{name}            - update flag value (operator only)
  DELETE /flags/{name}           - reset flag to env default (operator only)

  GET  /flags/modes/             - list all execution modes
  GET  /flags/modes/{name}       - get single mode
  PUT  /flags/modes/{name}       - create or update a mode (operator only)
  DELETE /flags/modes/{name}     - delete a mode (operator only)

Auth: all write operations require X-Admin-Email header pointing to
      a SecurityUser with is_admin=True (reuses existing admin auth pattern).
"""

import logging

from core.db.engine import get_session
from core.models.models import (
    ExecutionModeRead,
    ExecutionModeUpsert,
    FeatureFlagRead,
    FeatureFlagUpdate,
    SecurityUser,
)
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlmodel import Session

from app.services.auth import require_admin
from app.services.flag_service import FlagService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/flags", tags=["feature_flags"])

# Singleton service wired to the app's Redis
_flag_service: FlagService | None = None


def get_flag_service() -> FlagService:
    global _flag_service
    if _flag_service is None:
        _flag_service = FlagService()
    return _flag_service


def _get_admin(
    x_admin_email: str = Header(..., alias="X-Admin-Email"),
    session: Session = Depends(get_session),
) -> SecurityUser:
    """Dependency: validates the caller is an active admin (operator role)."""
    return require_admin(x_admin_email, session)


# ── Feature Flag endpoints ────────────────────────────────────────────────────


@router.get("/", response_model=list[FeatureFlagRead])
def list_flags(
    current_admin: SecurityUser = Depends(_get_admin),
    svc: FlagService = Depends(get_flag_service),
):
    """List all feature flags with their current values and metadata."""
    return svc.list_all()


@router.get("/map")
def get_flag_map(svc: FlagService = Depends(get_flag_service)):
    """
    Return a flat {name: value} dict for all flags.
    Used by the agent's FlagBridge. No admin auth required (internal service call).
    """
    return svc.get_map()


@router.get("/{name}", response_model=FeatureFlagRead)
def get_flag(
    name: str,
    current_admin: SecurityUser = Depends(_get_admin),
    svc: FlagService = Depends(get_flag_service),
):
    """Get a single flag by name."""
    flags = svc.list_all()
    flag = next((f for f in flags if f.name == name), None)
    if flag is None:
        raise HTTPException(status_code=404, detail=f"Flag '{name}' not found")
    return flag


@router.patch("/{name}", response_model=FeatureFlagRead)
def update_flag(
    name: str,
    body: FeatureFlagUpdate,
    current_admin: SecurityUser = Depends(_get_admin),
    svc: FlagService = Depends(get_flag_service),
):
    """
    Update a flag's value. Enforces type validation.
    Returns 422 if value type does not match the flag's declared type.
    All changes are audited with actor email and timestamp.
    """
    logger.info("Admin '%s' updating flag '%s'", current_admin.email, name)
    return svc.set(name, body.value, actor=current_admin.email)


@router.delete("/{name}", status_code=204)
def reset_flag(
    name: str,
    current_admin: SecurityUser = Depends(_get_admin),
    svc: FlagService = Depends(get_flag_service),
):
    """
    Reset a flag to its env-var default by clearing the DB override value.
    Writes an audit record with new_value=null to mark the reset event.
    """
    logger.info(
        "Admin '%s' resetting flag '%s' to env default", current_admin.email, name
    )
    svc.delete(name, actor=current_admin.email)


# ── Execution Mode endpoints ──────────────────────────────────────────────────


@router.get("/modes/", response_model=list[ExecutionModeRead])
def list_modes(
    current_admin: SecurityUser = Depends(_get_admin),
    svc: FlagService = Depends(get_flag_service),
):
    """List all execution modes."""
    return svc.list_modes()


@router.get("/modes/map")
def get_modes_map(svc: FlagService = Depends(get_flag_service)):
    """
    Return a flat list of active mode names.
    Used by the agent and Studio to populate the execution_mode selector.
    No admin auth required.
    """
    modes = svc.list_modes()
    return [
        {"name": m.name, "description": m.description, "is_active": m.is_active}
        for m in modes
    ]


@router.get("/modes/{name}", response_model=ExecutionModeRead)
def get_mode(
    name: str,
    svc: FlagService = Depends(get_flag_service),
):
    """Get a single execution mode (flag_overrides included). No admin auth required."""
    mode = svc.get_mode(name)
    if mode is None:
        raise HTTPException(
            status_code=404, detail=f"Execution mode '{name}' not found"
        )
    return mode


@router.put("/modes/{name}", response_model=ExecutionModeRead)
def upsert_mode(
    name: str,
    body: ExecutionModeUpsert,
    current_admin: SecurityUser = Depends(_get_admin),
    svc: FlagService = Depends(get_flag_service),
):
    """Create or update an execution mode (operator only)."""
    logger.info("Admin '%s' upserting execution mode '%s'", current_admin.email, name)
    return svc.upsert_mode(name, body, actor=current_admin.email)


@router.delete("/modes/{name}", status_code=204)
def delete_mode(
    name: str,
    current_admin: SecurityUser = Depends(_get_admin),
    svc: FlagService = Depends(get_flag_service),
):
    """Delete an execution mode (operator only)."""
    logger.info("Admin '%s' deleting execution mode '%s'", current_admin.email, name)
    svc.delete_mode(name)
