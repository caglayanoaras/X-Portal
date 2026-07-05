from typing import Annotated

from fastapi import (
    APIRouter, Request, Depends, status, HTTPException
)
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.core.config import settings
from app.core.database import get_session
from app.core.exceptions import (
    DuplicateResourceError, ResourceNotFoundError, ActionForbiddenError
)
from app.crud.users_and_permissions import *
from app.models import *
from app.dependencies.auth import (
    get_current_active_user, get_current_superadmin
)

# Jinja2 templates
templates = Jinja2Templates(directory="app/templates")

# Dependencies
UserDep = Annotated[UserRead, Depends(get_current_superadmin)]
ActiveUserDep = Annotated[User, Depends(get_current_active_user)]
SessionDep = Annotated[Session, Depends(get_session)]

users_and_permissions_router = APIRouter(
    prefix="/users_and_permissions",
    responses={
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        404: {"description": "Not Found"},
    },
    tags=['Users and Permissions']
)


@users_and_permissions_router.get("/", name='users_and_permissions_index', include_in_schema=False, response_class=HTMLResponse)
async def users_and_permissions_index(request: Request, current_user: UserDep):
    """Main html page of the module."""
    
    # Updated to the new Starlette/FastAPI TemplateResponse signature
    return templates.TemplateResponse(
        request=request,
        name="users_and_permissions.html", 
        context={
            "user_types": UserType,
            "user": current_user,
        }
    )

# region UserRole Operations
@users_and_permissions_router.get("/userroles/", name='get_all_user_roles', response_model=list[UserRoleRead])
def get_all_user_roles(current_user: UserDep, db: SessionDep):
    """Retrieve all user roles."""
    return read_all_roles(db)


@users_and_permissions_router.get("/userroles/{role_id}", response_model=UserRoleRead)
def get_user_role_by_id(current_user: UserDep, role_id: int, db: SessionDep):
    """Retrieve a specific user role by its ID."""
    role = read_role(db, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User role not found")
    return role


@users_and_permissions_router.post("/userroles/", response_model=UserRole, status_code=status.HTTP_201_CREATED)
def create_new_user_role(current_user: UserDep, role_create: UserRoleCreate, db: SessionDep):
    """Create a new user role."""
    try:
        return create_role(db=db, role_create=role_create)
    except DuplicateResourceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@users_and_permissions_router.put("/userroles/{role_id}", name='update_existing_user_role', response_model=UserRole)
def update_existing_user_role(current_user: UserDep, role_id: int, role_update: UserRoleUpdate, db: SessionDep):
    """Update an existing user role."""
    db_role = read_role(db, role_id)
    if not db_role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User role not found")
        
    try:
        return update_role(db=db, db_role=db_role, input_role=role_update)
    except DuplicateResourceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@users_and_permissions_router.delete("/userroles/{role_id}", name='delete_user_role_by_id', status_code=status.HTTP_204_NO_CONTENT)
def delete_user_role_by_id(current_user: UserDep, role_id: int, db: SessionDep):
    """Delete a user role by its ID."""
    if not delete_role(db, role_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User role not found")
    return None
# endregion


# region UserSkill Operations
@users_and_permissions_router.get("/userskills/", name='get_all_user_skills', response_model=list[UserSkillRead])
def get_all_user_skills(current_user: UserDep, db: SessionDep):
    """Retrieve all user skills."""
    return read_all_skills(db)


@users_and_permissions_router.get("/userskills/{skill_id}", response_model=UserSkillRead)
def get_user_skill_by_id(current_user: UserDep, skill_id: int, db: SessionDep):
    """Retrieve a specific user skill by its ID."""
    skill = read_skill(db, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User skill not found")
    return skill


@users_and_permissions_router.post("/userskills/", response_model=UserSkill, status_code=status.HTTP_201_CREATED)
def create_new_user_skill(current_user: UserDep, skill_create: UserSkillCreate, db: SessionDep):
    """Create a new user skill."""
    try:
        return create_skill(db=db, skill_create=skill_create)
    except DuplicateResourceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@users_and_permissions_router.put("/userskills/{skill_id}", name="update_existing_user_skill", response_model=UserSkill)
def update_existing_user_skill(current_user: UserDep, skill_id: int, skill_update: UserSkillUpdate, db: SessionDep):
    """Update an existing user skill."""
    db_skill = read_skill(db, skill_id)
    if not db_skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User skill not found")
        
    try:
        return update_skill(db=db, db_skill=db_skill, input_skill=skill_update)
    except DuplicateResourceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@users_and_permissions_router.delete("/userskills/{skill_id}", name="delete_user_skill_by_id", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_skill_by_id(current_user: UserDep, skill_id: int, db: SessionDep):
    """Delete a user skill by its ID."""
    if not delete_skill(db, skill_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User skill not found")
    return None
# endregion


# region User Operations
@users_and_permissions_router.get("/users/", name="get_all_users", response_model=list[UserRead])
def get_all_users(current_user: ActiveUserDep, db: SessionDep):
    """Retrieve all users."""
    return read_all_users(db)


@users_and_permissions_router.get("/users/{username}", response_model=UserRead)
def get_user_by_username(current_user: ActiveUserDep, username: str, db: SessionDep):
    """Retrieve a specific user by its username."""
    user = read_user(db, username)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@users_and_permissions_router.post("/users/", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_new_user(current_user: UserDep, user_create: UserCreate, db: SessionDep):
    """Create a new user."""
    try:
        return create_user(db=db, user_create=user_create, created_by=current_user.username)
    except DuplicateResourceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@users_and_permissions_router.put("/users/{username}", name='update_existing_user', response_model=UserRead)
def update_existing_user(current_user: UserDep, username: str, user_update: UserUpdate, db: SessionDep):
    """Update an existing user."""
    db_user = read_user(db, username)
    if not db_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        
    try:
        return update_user(db=db, db_user=db_user, user_update=user_update, updated_by=current_user.username)
    except DuplicateResourceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@users_and_permissions_router.delete("/users/{username}", name='delete_user_by_username', status_code=status.HTTP_204_NO_CONTENT)
def delete_user_by_username(current_user: UserDep, username: str, db: SessionDep):
    """Delete a user by its username."""
    try:
        delete_user(db=db, username=username)
        return None
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ActionForbiddenError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
# endregion


# region Module Operations
@users_and_permissions_router.get("/modules/", name='get_all_modules', response_model=list[ModuleRead])
def get_all_modules(current_user: UserDep, db: SessionDep):
    """Retrieve all modules."""
    return read_all_modules(db)
# endregion