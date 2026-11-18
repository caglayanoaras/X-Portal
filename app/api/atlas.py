"""Atlas pages and JSON API.

Everything the pages do goes through the API under /atlas/api, so the browser
and scripts follow the same rules. Sign in as for the rest of the portal: the
session cookie, or `Authorization: Bearer <token>` from POST /auth/token.

    GET    /atlas/api/roles                              portal roles (for role pickers)
    GET    /atlas/api/classes                            classes you can see
    POST   /atlas/api/classes                            create a class          (admins)
    GET    /atlas/api/classes/{class}                    its definition
    PUT    /atlas/api/classes/{class}[?force=true]       replace it              (admins)
    DELETE /atlas/api/classes/{class}[?force=true]       delete it               (admins)
    GET    /atlas/api/classes/{class}/objects            list, filter, search, sort, page
    POST   /atlas/api/classes/{class}/objects            create an object
    GET    /atlas/api/classes/{class}/objects/{id}       one object
    PATCH  /atlas/api/classes/{class}/objects/{id}       change some fields
    DELETE /atlas/api/classes/{class}/objects/{id}       delete it
    PUT    /atlas/api/classes/{class}/objects/{id}/files/{attribute}   upload an image or file
    GET    /atlas/api/classes/{class}/objects/{id}/files/{attribute}   get it [?view=thumb|preview][&download=true]
    DELETE /atlas/api/classes/{class}/objects/{id}/files/{attribute}   remove it

Admins are Data Admins and Superadmins. The rules for everyone else are in
app/atlas/permissions.py.
"""
import os
from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.atlas import db as atlas_db
from app.atlas import files
from app.atlas.errors import AtlasError
from app.atlas.objects import (PAGE_SIZE, PAGE_SIZE_MAX, class_visible, create_object, delete_object,
                               get_file, get_object, list_objects, set_file, update_object, upload_attribute)
from app.atlas.permissions import can_create, is_atlas_admin, viewer_for
from app.atlas.schema import (RESERVED_KEYS, class_to_api, create_class, delete_class, load_schema,
                              parse_definition, update_class)
from app.core.config import settings
from app.core.database import get_session
from app.core.utils import flash, get_flashed_messages, redirect_to_route
from app.dependencies.auth import get_current_active_user
from app.models import User, UserRole

templates = Jinja2Templates(directory="app/templates")


def asset_version(path: str) -> str:
    """A version for a file under app/static (its modification time). The pages add it
    to their script and style URLs (?v=...), so a changed file gets a new URL and no
    browser can pair a new page with an old cached copy."""
    try:
        return str(int(os.path.getmtime(os.path.join("app", "static", path))))
    except OSError:
        return "0"


templates.env.globals["asset_version"] = asset_version

ActiveUserDep = Annotated[User, Depends(get_current_active_user)]
SessionDep = Annotated[Session, Depends(get_session)]


async def require_atlas_admin(current_user: ActiveUserDep) -> User:
    if not is_atlas_admin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only Data Admins and Superadmins can change the Atlas schema.")
    return current_user


AdminDep = Annotated[User, Depends(require_atlas_admin)]

atlas_router = APIRouter(
    prefix="/atlas",
    responses={
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        404: {"description": "Not Found"},
        409: {"description": "Conflict (stale version, data in the way, or a change that needs force=true)"},
    },
    tags=["Atlas"],
)


def _portal_role_ids(db: Session) -> set:
    return set(db.exec(select(UserRole.id)).all())


def _check_class_roles(roles: list, db: Session) -> None:
    known = _portal_role_ids(db) if roles else set()
    unknown = [role_id for role_id in roles if role_id not in known]
    if unknown:
        raise AtlasError(400, "Unknown role id(s): " + ", ".join(map(str, unknown)) + ".")


# region Pages
@atlas_router.get("/", name="atlas_index", include_in_schema=False, response_class=HTMLResponse)
def atlas_index(request: Request, current_user: ActiveUserDep):
    """The Atlas browser: classes on the left, their objects in a grid."""
    return templates.TemplateResponse(
        request=request,
        name="atlas.html",
        context={
            "user": current_user,
            "is_atlas_admin": is_atlas_admin(current_user),
            "flash_messages": get_flashed_messages(request),
            "max_file_mb": settings.ATLAS_MAX_FILE_MB,
            "image_accept": files.IMAGE_ACCEPT,
            "file_accept": files.DOCUMENT_ACCEPT,
        },
    )


@atlas_router.get("/admin", name="atlas_admin_page", include_in_schema=False, response_class=HTMLResponse)
def atlas_admin_page(request: Request, current_user: ActiveUserDep):
    """The class builder, for Data Admins and Superadmins."""
    if not is_atlas_admin(current_user):
        flash(request, "Only Data Admins and Superadmins can open the Atlas class builder.", "error")
        return redirect_to_route(request, "atlas_index")
    return templates.TemplateResponse(
        request=request,
        name="atlas_admin.html",
        context={"user": current_user, "is_atlas_admin": True},
    )
# endregion


# region Schema API
@atlas_router.get("/api/roles", name="atlas_api_roles")
def api_roles(current_user: ActiveUserDep, db: SessionDep):
    """Portal roles with their permission flags."""
    return [{"id": r.id, "rolename": r.rolename, "can_create": r.can_create, "can_read": r.can_read,
             "can_update": r.can_update, "can_delete": r.can_delete}
            for r in db.exec(select(UserRole).order_by(UserRole.rolename)).all()]


@atlas_router.get("/api/classes", name="atlas_api_classes")
def api_classes(current_user: ActiveUserDep):
    """The classes you can see (admins see all), ordered by folder and label."""
    viewer = viewer_for(current_user)
    with atlas_db.connect() as conn:
        schema = load_schema(conn)
        return [{"key": c.key, "label": c.label, "path": c.path, "description": c.description,
                 "color": c.color, "icon": c.icon, "permissions": {"create": can_create(viewer, c)}}
                for c in schema.classes.values() if class_visible(conn, viewer, c)]


@atlas_router.post("/api/classes", name="atlas_api_create_class", status_code=status.HTTP_201_CREATED)
def api_create_class(current_user: AdminDep, db: SessionDep, payload: dict = Body(...)):
    """Create a class. Body: key, label, path, description, color ("#21358b"), icon ("fa-flask"),
    roles, attributes, relationships."""
    cls = parse_definition(payload)
    _check_class_roles(cls.roles, db)
    with atlas_db.connect() as conn:
        create_class(conn, load_schema(conn), cls, current_user.username)
        schema = load_schema(conn)
        return _class_out(conn, schema.get(cls.key), schema, current_user)


@atlas_router.get("/api/classes/{class_key}", name="atlas_api_class")
def api_class(class_key: str, current_user: ActiveUserDep):
    """A class definition, plus what you may do with it (permissions)."""
    viewer = viewer_for(current_user)
    with atlas_db.connect() as conn:
        schema = load_schema(conn)
        cls = schema.get(class_key)
        if not class_visible(conn, viewer, cls):
            raise AtlasError(404, f"There is no class '{class_key}'.")
        return _class_out(conn, cls, schema, current_user)


@atlas_router.put("/api/classes/{class_key}", name="atlas_api_update_class")
def api_update_class(class_key: str, current_user: AdminDep, db: SessionDep,
                     payload: dict = Body(...), force: bool = False):
    """Replace a class definition (keys can't change). Changes that delete data
    answer 409 with `warnings` and `requires_force`; repeat with force=true to apply."""
    cls = parse_definition(payload, key=class_key)
    _check_class_roles(cls.roles, db)
    with atlas_db.connect() as conn:
        update_class(conn, load_schema(conn), cls, current_user.username, force=force)
        schema = load_schema(conn)
        return _class_out(conn, schema.get(class_key), schema, current_user)


@atlas_router.delete("/api/classes/{class_key}", name="atlas_api_delete_class",
                     status_code=status.HTTP_204_NO_CONTENT)
def api_delete_class(class_key: str, current_user: AdminDep, force: bool = False):
    """Delete a class and its objects (objects need force=true)."""
    with atlas_db.connect() as conn:
        delete_class(conn, load_schema(conn), class_key, force=force)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _class_out(conn, cls, schema, user) -> dict:
    viewer = viewer_for(user)
    out = class_to_api(cls, schema, admin=viewer.is_admin)
    out["permissions"] = {"create": can_create(viewer, cls), "admin": viewer.is_admin}
    return out
# endregion


# region Objects API
@atlas_router.get("/api/classes/{class_key}/objects", name="atlas_api_objects")
def api_objects(
    class_key: str,
    request: Request,
    current_user: ActiveUserDep,
    q: str | None = Query(None, description="Search: every word must appear in some field."),
    sort: str | None = Query(None, description="Comma list of fields, '-' for descending: -_created_at,sample"),
    page: int = Query(1, ge=1),
    page_size: int = Query(PAGE_SIZE, ge=1, le=PAGE_SIZE_MAX),
    expand: str | None = Query(None, description="Relationships to return as {_id, _title}: '*' or a comma list."),
    fields: str | None = Query(None, description="Only these fields (comma list); _id is always included."),
    incoming_counts: bool = Query(False, description="Add _incoming_counts: per 'class.relationship' that "
                                                     "points here, how many objects you can read link to each one."),
):
    """List objects. Any other query parameter is a filter: `field=value` or
    `field__op=value` with op in eq, ne, gt, gte, lt, lte, contains, startswith,
    in (comma list), isnull (true/false), on (a date). Examples:
    `result=fail`, `qty__gte=5`, `retests=9`, `_created_at__gte=2026-11-01`.
    The objects linking to object 9 through batches.retests: GET /classes/batches/objects?retests=9."""
    viewer = viewer_for(current_user)
    filters = [(k, v) for k, v in request.query_params.multi_items() if k not in RESERVED_KEYS]
    with atlas_db.connect() as conn:
        schema = load_schema(conn)
        cls = schema.get(class_key)
        return list_objects(conn, schema, cls, viewer, filters, text=q, sort=sort, page=page,
                            page_size=page_size, expand=expand, fields=fields, incoming_counts=incoming_counts)


@atlas_router.post("/api/classes/{class_key}/objects", name="atlas_api_create_object",
                   status_code=status.HTTP_201_CREATED)
def api_create_object(class_key: str, current_user: ActiveUserDep, db: SessionDep, payload: dict = Body(...)):
    """Create an object: attribute keys, relationship keys (an id or a list of ids)
    and, for admins, _roles."""
    viewer = viewer_for(current_user)
    valid_roles = _portal_role_ids(db) if payload.get("_roles") else set()
    with atlas_db.connect() as conn:
        schema = load_schema(conn)
        cls = schema.get(class_key)
        object_id = create_object(conn, schema, cls, viewer, payload, valid_roles)
        try:
            return get_object(conn, schema, cls, viewer, object_id)
        except AtlasError:                      # created, but not readable by its creator
            return {"_id": object_id}


@atlas_router.get("/api/classes/{class_key}/objects/{object_id}", name="atlas_api_object")
def api_object(class_key: str, object_id: int, current_user: ActiveUserDep, expand: str | None = None):
    """One object, with `_incoming`: the objects (you can read) that link to it."""
    viewer = viewer_for(current_user)
    with atlas_db.connect() as conn:
        schema = load_schema(conn)
        return get_object(conn, schema, schema.get(class_key), viewer, object_id, expand=expand)


@atlas_router.patch("/api/classes/{class_key}/objects/{object_id}", name="atlas_api_update_object")
def api_update_object(class_key: str, object_id: int, current_user: ActiveUserDep, db: SessionDep,
                      payload: dict = Body(...), expand: str | None = None):
    """Change the fields you send. Include "_version" (from your last read) to
    get a 409 instead of overwriting someone else's change."""
    viewer = viewer_for(current_user)
    valid_roles = _portal_role_ids(db) if "_roles" in payload else set()
    with atlas_db.connect() as conn:
        schema = load_schema(conn)
        cls = schema.get(class_key)
        update_object(conn, schema, cls, viewer, object_id, payload, valid_roles)
        return get_object(conn, schema, cls, viewer, object_id, expand=expand)


@atlas_router.delete("/api/classes/{class_key}/objects/{object_id}", name="atlas_api_delete_object",
                     status_code=status.HTTP_204_NO_CONTENT)
def api_delete_object(class_key: str, object_id: int, current_user: ActiveUserDep):
    """Delete an object; its links and uploads go with it."""
    viewer = viewer_for(current_user)
    with atlas_db.connect() as conn:
        schema = load_schema(conn)
        delete_object(conn, schema, schema.get(class_key), viewer, object_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
# endregion


# region Files API
@atlas_router.put("/api/classes/{class_key}/objects/{object_id}/files/{attr_key}", name="atlas_api_put_file")
def api_put_file(class_key: str, object_id: int, attr_key: str, current_user: ActiveUserDep,
                 file: UploadFile = File(..., description="The image or file (multipart form field 'file')."),
                 version: int | None = Form(None, alias="_version",
                                            description="Fail with 409 if the object changed since this version.")):
    """Upload an image or file into an attribute, replacing what was there.
    Images: PNG, JPEG, GIF, WebP, TIFF or BMP. Files: PDF, Office, OpenDocument or CSV.
    Answers with the object, so you get its new _version."""
    limit = settings.ATLAS_MAX_FILE_MB * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise AtlasError(413, f"'{files.clean_name(file.filename)}' is larger than {settings.ATLAS_MAX_FILE_MB} MB.")
    viewer = viewer_for(current_user)
    with atlas_db.connect() as conn:
        schema = load_schema(conn)
        cls = schema.get(class_key)
        attr = upload_attribute(cls, attr_key)
        set_file(conn, cls, viewer, object_id, attr_key, files.prepare(attr.type, file.filename, data), version)
        return get_object(conn, schema, cls, viewer, object_id)


@atlas_router.delete("/api/classes/{class_key}/objects/{object_id}/files/{attr_key}", name="atlas_api_delete_file")
def api_delete_file(class_key: str, object_id: int, attr_key: str, current_user: ActiveUserDep,
                    version: int | None = Query(None, alias="_version",
                                                description="Fail with 409 if the object changed since this version.")):
    """Remove the image or file in an attribute. Answers with the object."""
    viewer = viewer_for(current_user)
    with atlas_db.connect() as conn:
        schema = load_schema(conn)
        cls = schema.get(class_key)
        set_file(conn, cls, viewer, object_id, attr_key, None, version)
        return get_object(conn, schema, cls, viewer, object_id)


@atlas_router.get("/api/classes/{class_key}/objects/{object_id}/files/{attr_key}", name="atlas_api_file")
def api_file(class_key: str, object_id: int, attr_key: str, request: Request, current_user: ActiveUserDep,
             view: str = Query("original", description="original, preview (what a browser can show) or thumb."),
             download: bool = Query(False, description="Save it instead of opening it in the browser.")):
    """The image or file in an attribute. Images and PDFs open in the browser unless
    download=true; Office files always download."""
    viewer = viewer_for(current_user)
    with atlas_db.connect() as conn:
        schema = load_schema(conn)
        stored = get_file(conn, schema.get(class_key), viewer, object_id, attr_key, view)
    etag = f'"{stored["sha256"][:32]}-{view}"'
    inline = not download and (stored["type"].startswith("image/") or stored["type"] in files.INLINE_TYPES)
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff",
               "Content-Disposition": files.content_disposition(stored["name"], inline)}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=stored["body"], media_type=stored["type"], headers=headers)
# endregion
