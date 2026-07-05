from typing import Annotated

from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

from app.models import User, UserType
from app.dependencies.auth import get_current_active_user

# Jinja2 templates
templates = Jinja2Templates(directory="app/templates")

# Dependencies
ActiveUserDep = Annotated[User, Depends(get_current_active_user)]

data_panels_router = APIRouter(
    prefix="/data_panels",
    responses={
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        404: {"description": "Not Found"},
    },
    tags=['Data Panels']
)


@data_panels_router.get("/", name='data_panels_index', include_in_schema=False, response_class=HTMLResponse)
async def data_panels_index(request: Request, current_user: ActiveUserDep):
    """Main html page of the Data Panels module."""

    return templates.TemplateResponse(
        request=request,
        name="data_panels.html",
        context={
            "user_types": UserType,
            "user": current_user,
        }
    )
