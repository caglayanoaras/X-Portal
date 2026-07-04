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

atlas_router = APIRouter(
    prefix="/atlas",
    responses={
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        404: {"description": "Not Found"},
    },
    tags=['Atlas']
)


@atlas_router.get("/", name='atlas_index', include_in_schema=False, response_class=HTMLResponse)
async def atlas_index(request: Request, current_user: ActiveUserDep):
    """Main html page of the module."""
    
    # Updated to the new Starlette/FastAPI TemplateResponse signature
    return templates.TemplateResponse(
        request=request,
        name="atlas.html", 
        context={
            "user_types": UserType,
            "user": current_user,
        }
    )
