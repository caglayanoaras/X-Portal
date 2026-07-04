from fastapi import (
    Depends, HTTPException, status, 
    APIRouter, Response, Query, 
    Cookie)
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import Session, select

import re
import jwt
from jwt.exceptions import InvalidTokenError
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from typing import Annotated
from pwdlib import PasswordHash

from app.core.config import settings
from app.models import User, UserRead, UserType
from app.core.database import get_session

auth_router = APIRouter(
    prefix="/auth",
    tags=['Authentication']
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token", auto_error=False)

pwd_context = PasswordHash.recommended()

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM

SessionDep = Annotated[Session, Depends(get_session)]

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: str

def get_user(db: Session, username: str) -> User|None:
    statement = select(User).where(User.username == username)
    userindb = db.exec(statement).first()
    if userindb:
        return userindb

def authenticate_user(db: Session, username: str, password: str) -> User|bool:
    userindb = get_user(db, username)
    if not userindb:
        return False
    if not verify_password(password, userindb.hashed_pw):
        return False
    return userindb

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt
    
async def get_current_user(
        db: SessionDep,
        token: Annotated[str|None, Depends(oauth2_scheme)],
        cookie_token: Annotated[str|None, Cookie(alias=settings.COOKIE_NAME, description="You only need to fill this parameter if you're not logged in. Otherwise, the cookie existing in your browser will provide it.")] = None
        ) -> UserRead:

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    auth_token = token or cookie_token
    
    if not auth_token:
        raise credentials_exception
    
    try:
        payload = jwt.decode(auth_token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception

    except InvalidTokenError:
        raise credentials_exception
    
    user = get_user(db, username=username)
    if user is None:
        raise credentials_exception
    return user

async def get_current_active_user(
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> UserRead:
    if current_user.is_active == False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
    return current_user

async def get_current_superadmin(
    current_user: Annotated[UserRead, Depends(get_current_active_user)]
) -> UserRead:
    """
    Dependency to get the current active user and check if they are a superadmin.

    Raises:
        HTTPException(403): If the user's usertype is not 'superadmin'.

    Returns:
        The user object if they are a superadmin.
    """
    # Check if the user object has the 'usertype' attribute and if it's 'superadmin'
    if getattr(current_user, "usertype", None) != UserType.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have the required permissions.",
        )
    return current_user

@auth_router.post("/token", name="login_for_access_token", response_model=Token)
async def login_for_access_token(
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: SessionDep,
    set_cookie: bool=True
) -> Token:
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )

    # Set secure cookie if requested
    if set_cookie:
        response.set_cookie(
            key=settings.COOKIE_NAME,
            value=access_token,
            max_age=settings.COOKIE_MAX_AGE,
            httponly=True,  # Prevents XSS attacks
            secure=settings.COOKIE_SECURE,    # Only send over HTTPS
            samesite=settings.COOKIE_SAMESITE,  # CSRF protection
        )
    
    return Token(access_token=access_token, token_type="bearer")

@auth_router.post("/logout", name='logout', response_class=RedirectResponse)
async def logout(response: Response):
    """Logout endpoint that clears the cookie"""
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(
        key=settings.COOKIE_NAME,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        path="/", 
    )
    return response
