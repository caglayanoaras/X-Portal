from typing import Annotated

from fastapi import APIRouter, Request, Depends, HTTPException, status, Query
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from sqlmodel import Session, SQLModel

from app.core.database import get_session
from app.core.exceptions import (
    DuplicateResourceError, ResourceNotFoundError, InvalidInputError, InvalidStateError,
)
from app.models import (
    User, UserType, ModuleKey,
    ActionTypeCreate, ActionTypeUpdate, ActionTypeRead,
    ActionCreate, ActionValuesUpdate, ActionRead,
)
from app.dependencies.auth import (
    get_current_active_user, get_current_superadmin,
    require_module_admin, is_module_admin,
)
from app.crud import actions as crud
from app.crud import module_admin

# This module's key, used by the shared module-admin machinery.
MODULE = ModuleKey.actions

templates = Jinja2Templates(directory="app/templates")

ActiveUserDep = Annotated[User, Depends(get_current_active_user)]
AdminDep = Annotated[User, Depends(require_module_admin(ModuleKey.actions))]
SuperadminDep = Annotated[User, Depends(get_current_superadmin)]
SessionDep = Annotated[Session, Depends(get_session)]


class AdminRolesUpdate(SQLModel):
    role_ids: list[int] = []

actions_router = APIRouter(
    prefix="/actions",
    responses={
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        404: {"description": "Not Found"},
        409: {"description": "Conflict / invalid state"},
    },
    tags=['Actions'],
)


# region Pages
@actions_router.get("/", name="actions_index", include_in_schema=False, response_class=HTMLResponse)
async def actions_index(request: Request, current_user: ActiveUserDep, db: SessionDep):
    """The user-facing "My Actions" page."""
    return templates.TemplateResponse(
        request=request, name="actions.html",
        context={
            "user": current_user,
            "is_action_admin": is_module_admin(db, current_user, MODULE),
        },
    )


@actions_router.get("/admin", name="actions_admin_page", include_in_schema=False, response_class=HTMLResponse)
async def actions_admin_page(request: Request, current_user: AdminDep):
    """The admin page: manage action types + view everyone's actions."""
    return templates.TemplateResponse(
        request=request, name="actions_admin.html",
        context={
            "user": current_user,
            "is_action_admin": True,
            "is_superadmin": current_user.usertype == UserType.superadmin,
        },
    )


# --- Permissions (superadmin only): which roles may open the admin area ---
@actions_router.get("/admin/roles", name="get_actions_admin_roles", include_in_schema=False)
def get_actions_admin_roles(current_user: SuperadminDep, db: SessionDep):
    return {
        "roles": [{"id": r.id, "rolename": r.rolename} for r in module_admin.list_all_roles(db)],
        "admin_role_ids": module_admin.get_admin_role_ids(db, MODULE),
    }


@actions_router.put("/admin/roles", name="set_actions_admin_roles", include_in_schema=False)
def set_actions_admin_roles(current_user: SuperadminDep, payload: AdminRolesUpdate, db: SessionDep):
    module_admin.set_admin_roles(db=db, module_key=MODULE, role_ids=payload.role_ids)
    return {"admin_role_ids": module_admin.get_admin_role_ids(db, MODULE)}
# endregion


# region Action Types (admin-managed catalog)
@actions_router.get("/types/", name="get_active_action_types", response_model=list[ActionTypeRead])
def get_active_action_types(current_user: ActiveUserDep, db: SessionDep):
    """Active types only — feeds the user's "New Action" dropdown."""
    return crud.read_all_action_types(db, include_inactive=False)


@actions_router.get("/types/all", name="get_all_action_types", response_model=list[ActionTypeRead])
def get_all_action_types(current_user: AdminDep, db: SessionDep):
    """All types incl. retired — for the admin management grid."""
    return crud.read_all_action_types(db, include_inactive=True)


@actions_router.post("/types/", name="create_action_type", response_model=ActionTypeRead,
                     status_code=status.HTTP_201_CREATED)
def create_action_type(current_user: AdminDep, data: ActionTypeCreate, db: SessionDep):
    try:
        return crud.create_action_type(db=db, data=data, created_by=current_user.username)
    except (DuplicateResourceError, InvalidInputError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@actions_router.get("/types/{type_id}", name="get_action_type", response_model=ActionTypeRead)
def get_action_type(current_user: AdminDep, type_id: int, db: SessionDep):
    atype = crud.read_action_type(db, type_id)
    if not atype:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action type not found")
    return atype


@actions_router.put("/types/{type_id}", name="update_action_type", response_model=ActionTypeRead)
def update_action_type(current_user: AdminDep, type_id: int, data: ActionTypeUpdate, db: SessionDep):
    atype = crud.read_action_type(db, type_id)
    if not atype:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action type not found")
    try:
        return crud.update_action_type(db=db, atype=atype, data=data, modified_by=current_user.username)
    except (DuplicateResourceError, InvalidInputError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@actions_router.delete("/types/{type_id}", name="delete_action_type")
def delete_action_type(current_user: AdminDep, type_id: int, db: SessionDep):
    try:
        result = crud.delete_action_type(db=db, type_id=type_id)
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return {"result": result, "detail": f"Action type {result}."}
# endregion


# region Actions (user instances)
@actions_router.get("/mine/", name="get_my_actions", response_model=list[ActionRead])
def get_my_actions(current_user: ActiveUserDep, db: SessionDep):
    return [crud.serialize_action(a) for a in crud.read_my_actions(db, current_user.id)]


@actions_router.get("/all/", name="get_all_actions", response_model=list[ActionRead])
def get_all_actions(current_user: AdminDep, db: SessionDep,
                    action_type_id: int | None = Query(default=None)):
    return [crud.serialize_action(a) for a in crud.read_all_actions(db, action_type_id=action_type_id)]


@actions_router.post("/", name="create_action", response_model=ActionRead,
                     status_code=status.HTTP_201_CREATED)
def create_action(current_user: ActiveUserDep, data: ActionCreate, db: SessionDep):
    try:
        action = crud.create_action(
            db=db, user_id=current_user.id, action_type_id=data.action_type_id, values=data.values,
        )
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except InvalidInputError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return crud.serialize_action(action)


def _owned_action(db: Session, action_id: int, user: User):
    action = crud.read_action(db, action_id)
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
    if action.user_id != user.id and not is_module_admin(db, user, MODULE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This isn't your action")
    return action


@actions_router.get("/{action_id}", name="get_action", response_model=ActionRead)
def get_action(current_user: ActiveUserDep, action_id: int, db: SessionDep):
    return crud.serialize_action(_owned_action(db, action_id, current_user))


@actions_router.put("/{action_id}", name="update_action", response_model=ActionRead)
def update_action(current_user: ActiveUserDep, action_id: int, data: ActionValuesUpdate, db: SessionDep):
    action = crud.read_action(db, action_id)
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
    if action.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This isn't your action")
    try:
        action = crud.update_action_values(db=db, action=action, values=data.values)
    except InvalidStateError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except InvalidInputError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return crud.serialize_action(action)


def _transition(fn, db: Session, action_id: int, user: User):
    action = crud.read_action(db, action_id)
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
    if action.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This isn't your action")
    try:
        action = fn(db=db, action=action)
    except InvalidStateError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return crud.serialize_action(action)


@actions_router.post("/{action_id}/start", name="start_action", response_model=ActionRead)
def start_action(current_user: ActiveUserDep, action_id: int, db: SessionDep):
    return _transition(crud.start_action, db, action_id, current_user)


@actions_router.post("/{action_id}/finish", name="finish_action", response_model=ActionRead)
def finish_action(current_user: ActiveUserDep, action_id: int, db: SessionDep):
    return _transition(crud.finish_action, db, action_id, current_user)


@actions_router.post("/{action_id}/cancel", name="cancel_action", response_model=ActionRead)
def cancel_action(current_user: ActiveUserDep, action_id: int, db: SessionDep):
    return _transition(crud.cancel_action, db, action_id, current_user)
# endregion
