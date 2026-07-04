import os
import uuid
from typing import Annotated

from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlmodel import Session, select
from fastapi_standalone_docs import StandaloneDocs

from app.models import UserRead, User
from app.core.database import init_db, get_session
from app.dependencies.auth import (
    auth_router, get_current_active_user,
    verify_password, get_password_hash
)
from app.core.config import settings
from app.core.utils import (
    flash, get_flashed_messages,
    redirect_to_route, is_valid_email
)
from app.api.users_and_permissions import users_and_permissions_router
from app.api.atlas import atlas_router


# region Type Aliases (Dependencies)
SessionDep = Annotated[Session, Depends(get_session)]
ActiveUserDep = Annotated[UserRead, Depends(get_current_active_user)]
# endregion


async def lifespan(app: FastAPI):
    # Load the ML model & DB
    init_db()
    yield

app = FastAPI(
    lifespan=lifespan, 
    title=f'{settings.COMPANY_NAME} Portal', 
    description=f'A portal for {settings.COMPANY_NAME} modules', 
    version='1.0.0'
)

StandaloneDocs(app=app)

# Middleware
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

# Mount static files & templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Include Routers
app.include_router(auth_router)
app.include_router(users_and_permissions_router)
app.include_router(atlas_router)


# region Pages
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def login_form(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="login.html",
        context={
                "company_name": settings.COMPANY_NAME,
                "company_color1": settings.COMPANY_COLOR1,
                "company_color2": settings.COMPANY_COLOR2,
                "company_videos": settings.COMPANY_VIDEOS,
                }
    )


@app.get("/dashboard", include_in_schema=False, name='dashboard', response_class=HTMLResponse)
async def dashboard(request: Request, current_user: ActiveUserDep):
    """
    Show the dashboard with modules that *this* user can access.
    A user's access is defined by the many-to-many relationship
    User <-> UserModuleLink <-> Module.
    """
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html", 
        context={"user": current_user}
    )


@app.get("/user_profile", include_in_schema=False, name='user_profile', response_class=HTMLResponse)
async def user_profile(request: Request, current_user: ActiveUserDep):
    """Render the user profile page."""
    
    flash_messages = get_flashed_messages(request)
    return templates.TemplateResponse(
        request=request,
        name="user_profile.html", 
        context={
            "user": current_user,
            "flash_messages": flash_messages
        }
    )
# endregion


# region Form Actions
@app.post("/update_profile", include_in_schema=False, name="update_profile", response_class=RedirectResponse)
async def update_profile(
    request: Request,
    email: str = Form(...),
    profile_image: UploadFile = File(None),
    current_user: ActiveUserDep = None,
    db: SessionDep = None,
):
    try:
        # Refresh user data for writing
        user = db.exec(select(User).where(User.username == current_user.username)).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Check if email is being changed
        if email != user.email:
            existing_user = db.exec(select(User).where(User.email == email)).first()
            if existing_user and existing_user.username != user.username:
                flash(request, "Email is already in use by another account.", "error")
                return redirect_to_route(request, "user_profile")

            if not is_valid_email(email):
                flash(request, "Invalid email format.", "error")
                return redirect_to_route(request, "user_profile")

        user.email = email

        # Handle profile image upload
        images_path = os.path.join('app', 'static', 'images')
        if profile_image and profile_image.filename:
            
            allowed_types = ["image/jpeg", "image/png", "image/gif"]
            if profile_image.content_type not in allowed_types:
                flash(request, "Invalid image format. Please use JPEG, PNG, or GIF.", "error")
                return redirect_to_route(request, "user_profile")

            if profile_image.size > 5 * 1024 * 1024:
                flash(request, "File too large. Maximum size is 5MB.", "error")
                return redirect_to_route(request, "user_profile")

            ext = profile_image.filename.split(".")[-1]
            filename = f"{user.username}_profile_image_{uuid.uuid4().hex[:8]}.{ext}"
            file_path = os.path.join(images_path, filename)

            with open(file_path, "wb") as f:
                f.write(await profile_image.read())

            if user.profile_image_path != "default_profile_image.png":
                old_path = os.path.join(images_path, user.profile_image_path)
                if os.path.exists(old_path):
                    os.remove(old_path)

            user.profile_image_path = filename

        db.add(user)
        db.commit()

        flash(request, "Profile updated successfully!", "success")

    except Exception as e:
        flash(request, f"An error occurred: {str(e)}", "error")

    return redirect_to_route(request, "user_profile")


@app.post("/update_password", include_in_schema=False, name='update_password', response_class=RedirectResponse)
async def update_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    current_user: ActiveUserDep = None,
    db: SessionDep = None,
):
    try:
        user = db.exec(select(User).where(User.username == current_user.username)).first()
        if not user:
            flash(request, "User not found.", "error")
            return redirect_to_route(request, "user_profile")

        if new_password != confirm_password:
            flash(request, "New passwords do not match.", "error")
            return redirect_to_route(request, "user_profile")

        if not verify_password(current_password, user.hashed_pw):
            flash(request, "Current password is incorrect.", "error")
            return redirect_to_route(request, "user_profile")

        user.hashed_pw = get_password_hash(new_password)
        db.add(user)
        db.commit()

        flash(request, "Password changed successfully!", "success")

    except Exception as e:
        flash(request, f"An error occurred: {str(e)}", "error")

    return redirect_to_route(request, "user_profile")
# endregion