from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.exceptions import (
    DuplicateResourceError, ResourceNotFoundError,
    InvalidInputError, InvalidStateError,
)
from app.models import (
    ActionType, ActionTypeCreate, ActionTypeUpdate,
    Action, ActionStatus, ActionFieldType,
)

VALID_FIELD_TYPES = {t.value for t in ActionFieldType}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """Stored timestamps come back tz-naive (UTC); tag them so clients parse them as UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# region Validation
def validate_schema(schema: list[dict]) -> list[dict]:
    """Normalise + validate an ActionType's attribute schema."""
    cleaned, seen = [], set()
    for i, f in enumerate(schema):
        key = (f.get("key") or "").strip()
        label = (f.get("label") or "").strip()
        ftype = f.get("type")
        if not key:
            raise InvalidInputError(f"Field #{i + 1} is missing a key")
        if not label:
            raise InvalidInputError(f"Field '{key}' is missing a label")
        if key in seen:
            raise InvalidInputError(f"Duplicate field key '{key}'")
        seen.add(key)
        if ftype not in VALID_FIELD_TYPES:
            raise InvalidInputError(f"Field '{key}' has an invalid type '{ftype}'")
        options = None
        if ftype == "select":
            options = [str(o).strip() for o in (f.get("options") or []) if str(o).strip()]
            if not options:
                raise InvalidInputError(f"Select field '{key}' needs at least one option")
        cleaned.append({
            "key": key, "label": label, "type": ftype,
            "required": bool(f.get("required", False)), "options": options,
        })
    return cleaned


def validate_values(schema: list[dict], raw: dict) -> dict:
    """Validate + coerce a user's submitted attribute values against the schema."""
    raw = raw or {}
    cleaned = {}
    for f in schema:
        key, label, ftype = f["key"], f.get("label", f["key"]), f["type"]
        val = raw.get(key)
        is_empty = val is None or (isinstance(val, str) and val.strip() == "")
        if is_empty:
            if f.get("required") and ftype != "checkbox":
                raise InvalidInputError(f"'{label}' is required")
            cleaned[key] = False if ftype == "checkbox" else None
            continue
        if ftype == "number":
            try:
                val = float(val)
            except (TypeError, ValueError):
                raise InvalidInputError(f"'{label}' must be a number")
        elif ftype == "date":
            try:
                datetime.fromisoformat(str(val))
            except ValueError:
                raise InvalidInputError(f"'{label}' must be a valid date")
            val = str(val)
        elif ftype == "checkbox":
            val = val if isinstance(val, bool) else str(val).lower() in ("true", "on", "1", "yes")
        elif ftype == "select":
            val = str(val)
            if val not in (f.get("options") or []):
                raise InvalidInputError(f"'{label}' has an invalid selection")
        else:
            val = str(val)
        cleaned[key] = val
    return cleaned


def _check_display_field(display_field: str | None, schema: list[dict]) -> None:
    if display_field and display_field not in {f["key"] for f in schema}:
        raise InvalidInputError(f"Display field '{display_field}' is not one of the attributes")
# endregion


# region ActionType CRUD
def read_all_action_types(db: Session, include_inactive: bool = False) -> list[ActionType]:
    stmt = select(ActionType)
    if not include_inactive:
        stmt = stmt.where(ActionType.is_active == True)  # noqa: E712
    return db.exec(stmt.order_by(ActionType.name)).all()


def read_action_type(db: Session, type_id: int) -> ActionType | None:
    return db.get(ActionType, type_id)


def create_action_type(*, db: Session, data: ActionTypeCreate, created_by: str) -> ActionType:
    if db.exec(select(ActionType).where(ActionType.name == data.name)).first():
        raise DuplicateResourceError(f"Action type '{data.name}' already exists")
    schema = validate_schema([f.model_dump(mode="json") for f in data.attributes_schema])
    _check_display_field(data.display_field, schema)
    atype = ActionType(
        name=data.name, description=data.description, display_field=data.display_field,
        expected_duration_minutes=data.expected_duration_minutes, is_active=data.is_active,
        attributes_schema=schema, created_by=created_by, last_modified_by=created_by,
    )
    db.add(atype)
    db.commit()
    db.refresh(atype)
    return atype


def update_action_type(*, db: Session, atype: ActionType, data: ActionTypeUpdate, modified_by: str) -> ActionType:
    if data.name and data.name != atype.name:
        if db.exec(select(ActionType).where(ActionType.name == data.name)).first():
            raise DuplicateResourceError(f"Action type '{data.name}' already exists")
    payload = data.model_dump(exclude_unset=True, exclude={"attributes_schema"})
    for k, v in payload.items():
        setattr(atype, k, v)
    if data.attributes_schema is not None:
        atype.attributes_schema = validate_schema([f.model_dump(mode="json") for f in data.attributes_schema])
    _check_display_field(atype.display_field, atype.attributes_schema or [])
    atype.last_modified_by = modified_by
    atype.last_modified_at = _now()
    db.add(atype)
    db.commit()
    db.refresh(atype)
    return atype


def delete_action_type(*, db: Session, type_id: int) -> str:
    """Retire (soft-delete) a type that has recorded actions; hard-delete otherwise."""
    atype = db.get(ActionType, type_id)
    if not atype:
        raise ResourceNotFoundError("Action type not found")
    count = db.exec(select(func.count()).select_from(Action).where(Action.action_type_id == type_id)).one()
    if count and count > 0:
        atype.is_active = False
        db.add(atype)
        db.commit()
        return "retired"
    db.delete(atype)
    db.commit()
    return "deleted"
# endregion


# region Action CRUD
def _with_user(stmt):
    return stmt.options(selectinload(Action.user))


def create_action(*, db: Session, user_id: int, action_type_id: int, values: dict) -> Action:
    atype = db.get(ActionType, action_type_id)
    if not atype or not atype.is_active:
        raise ResourceNotFoundError("Action type not found or inactive")
    schema = atype.attributes_schema or []
    cleaned = validate_values(schema, values)
    snapshot = {"name": atype.name, "display_field": atype.display_field, "attributes_schema": schema}
    action = Action(
        action_type_id=atype.id, user_id=user_id,
        values=cleaned, type_snapshot=snapshot, status=ActionStatus.pending,
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def read_my_actions(db: Session, user_id: int) -> list[Action]:
    stmt = _with_user(select(Action).where(Action.user_id == user_id)).order_by(Action.created_at.desc())
    return db.exec(stmt).all()


def read_all_actions(db: Session, action_type_id: int | None = None) -> list[Action]:
    stmt = _with_user(select(Action))
    if action_type_id is not None:
        stmt = stmt.where(Action.action_type_id == action_type_id)
    return db.exec(stmt.order_by(Action.created_at.desc())).all()


def read_action(db: Session, action_id: int) -> Action | None:
    return db.exec(_with_user(select(Action).where(Action.id == action_id))).first()


def update_action_values(*, db: Session, action: Action, values: dict) -> Action:
    if action.status in (ActionStatus.completed, ActionStatus.cancelled):
        raise InvalidStateError("A completed or cancelled action can no longer be edited")
    schema = (action.type_snapshot or {}).get("attributes_schema", [])
    action.values = validate_values(schema, values)
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def start_action(*, db: Session, action: Action) -> Action:
    if action.status != ActionStatus.pending:
        raise InvalidStateError("Only a pending action can be started")
    action.status = ActionStatus.in_progress
    action.started_at = _now()
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def finish_action(*, db: Session, action: Action) -> Action:
    if action.status != ActionStatus.in_progress:
        raise InvalidStateError("Only an in-progress action can be finished")
    action.status = ActionStatus.completed
    action.finished_at = _now()
    started = _aware(action.started_at)
    if started:
        action.duration_seconds = int((action.finished_at - started).total_seconds())
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def cancel_action(*, db: Session, action: Action) -> Action:
    if action.status in (ActionStatus.completed, ActionStatus.cancelled):
        raise InvalidStateError("This action is already finished or cancelled")
    action.status = ActionStatus.cancelled
    action.cancelled_at = _now()
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def serialize_action(action: Action) -> dict:
    snap = action.type_snapshot or {}
    user = action.user
    return {
        "id": action.id,
        "action_type_id": action.action_type_id,
        "action_type_name": snap.get("name"),
        "user_id": action.user_id,
        "username": getattr(user, "username", None),
        "user_fullname": f"{user.name} {user.surname}" if user else None,
        "values": action.values or {},
        "type_snapshot": snap,
        "status": action.status,
        "created_at": _aware(action.created_at),
        "started_at": _aware(action.started_at),
        "finished_at": _aware(action.finished_at),
        "cancelled_at": _aware(action.cancelled_at),
        "duration_seconds": action.duration_seconds,
    }
# endregion
