from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from app.routes.explorer_routes import require_auth
from app.services.settings_service import configured_root, set_root

router = APIRouter(prefix="/api/settings")


class SettingsUpdate(BaseModel):
    root: str = Field(min_length=1, max_length=4096)


@router.get("")
def read_settings(user=Depends(require_auth)):
    return {"root": configured_root()}


@router.put("")
def update_settings(body: SettingsUpdate, user=Depends(require_auth)):
    return {"root": set_root(body.root), "ok": True}
