from typing import TypeVar, Type

from sqlalchemy.orm import selectinload
from sqlmodel import Session, select, SQLModel

from app.dependencies.auth import get_password_hash
from app.core.exceptions import DuplicateResourceError, ResourceNotFoundError, ActionForbiddenError
from app.models import (
    UserRole, UserRoleCreate, UserRoleUpdate, UserRoleRead,
    UserSkill, UserSkillCreate, UserSkillUpdate, UserSkillRead,
    User, UserCreate, UserType, UserUpdate, UserRead,
    Module, ModuleRead
)

T = TypeVar("T", bound=SQLModel)


# region Helpers
def _resolve_m2m_ids(db: Session, model_cls: Type[T], item_ids: list[int], name: str) -> list[T]:
    if not item_ids:
        return []
    
    items = db.exec(select(model_cls).where(model_cls.id.in_(item_ids))).all()
    
    if len(items) != len(set(item_ids)):
        missing = set(item_ids) - {item.id for item in items}
        raise ResourceNotFoundError(f"{name} not found: {missing}")
    return items
# endregion


# region UserRole CRUD
def read_role(db: Session, role_id: int) -> UserRoleRead | None:
    statement = select(UserRole).options(selectinload(UserRole.users)).where(UserRole.id == role_id)
    return db.exec(statement).first()

def read_all_roles(db: Session) -> list[UserRoleRead]:
    statement = select(UserRole).options(selectinload(UserRole.users))
    return db.exec(statement).all()

def create_role(*, db: Session, role_create: UserRoleCreate) -> UserRole:
    if db.exec(select(UserRole).where(UserRole.rolename == role_create.rolename)).first():
        raise DuplicateResourceError(f"Role '{role_create.rolename}' already exists")
        
    role = UserRole.model_validate(role_create)
    db.add(role)
    db.commit()
    db.refresh(role)
    return role

def update_role(*, db: Session, db_role: UserRole, input_role: UserRoleUpdate) -> UserRole:
    if input_role.rolename and input_role.rolename != db_role.rolename:
        if db.exec(select(UserRole).where(UserRole.rolename == input_role.rolename)).first():
            raise DuplicateResourceError(f"Role '{input_role.rolename}' already exists")
            
    role_data = input_role.model_dump(exclude_unset=True)
    db_role.sqlmodel_update(role_data)
    
    db.add(db_role)
    db.commit()
    db.refresh(db_role)
    return db_role

def delete_role(db: Session, role_id: int) -> bool:
    role = db.get(UserRole, role_id)
    if not role:
        return False
    db.delete(role)
    db.commit()
    return True
# endregion


# region UserSkill CRUD
def read_skill(db: Session, skill_id: int) -> UserSkillRead | None:
    statement = select(UserSkill).options(selectinload(UserSkill.users)).where(UserSkill.id == skill_id)
    return db.exec(statement).first()

def read_all_skills(db: Session) -> list[UserSkillRead]:
    statement = select(UserSkill).options(selectinload(UserSkill.users))
    return db.exec(statement).all()

def create_skill(*, db: Session, skill_create: UserSkillCreate) -> UserSkill:
    if db.exec(select(UserSkill).where(UserSkill.skillname == skill_create.skillname)).first():
        raise DuplicateResourceError(f"Skill '{skill_create.skillname}' already exists")
        
    skill = UserSkill.model_validate(skill_create)
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return skill

def update_skill(*, db: Session, db_skill: UserSkill, input_skill: UserSkillUpdate) -> UserSkill:
    if input_skill.skillname and input_skill.skillname != db_skill.skillname:
        if db.exec(select(UserSkill).where(UserSkill.skillname == input_skill.skillname)).first():
            raise DuplicateResourceError(f"Skill '{input_skill.skillname}' already exists")

    skill_data = input_skill.model_dump(exclude_unset=True)
    db_skill.sqlmodel_update(skill_data)

    db.add(db_skill)
    db.commit()
    db.refresh(db_skill)
    return db_skill

def delete_skill(db: Session, skill_id: int) -> bool:
    skill = db.get(UserSkill, skill_id)
    if not skill:
        return False
    db.delete(skill)
    db.commit()
    return True
# endregion


# region User CRUD
def read_user(db: Session, username: str) -> UserRead | None:
    statement = select(User).options(
        selectinload(User.roles),
        selectinload(User.modules),
        selectinload(User.skills)
    ).where(User.username == username)
    return db.exec(statement).first()

def read_all_users(db: Session) -> list[UserRead]:
    statement = select(User).options(
        selectinload(User.roles),
        selectinload(User.modules),
        selectinload(User.skills)
    )
    return db.exec(statement).all()

def create_user(*, db: Session, user_create: UserCreate, created_by: str) -> User:
    if db.exec(select(User).where(User.username == user_create.username)).first():
        raise DuplicateResourceError("Username already exists")

    user_data = user_create.model_dump(exclude={"pw", "roles", "modules", "skills", "divisions"})
    db_user = User(
        **user_data, 
        hashed_pw=get_password_hash(user_create.pw), 
        created_by=created_by, 
        last_modified_by=created_by
    )

    db_user.roles = _resolve_m2m_ids(db, UserRole, user_create.roles, "Roles")
    db_user.skills = _resolve_m2m_ids(db, UserSkill, user_create.skills, "Skills")

    if user_create.usertype == UserType.superadmin:
        db_user.modules = db.exec(select(Module)).all()
    elif user_create.modules:
        requested_ids = set(user_create.modules)
        restricted_module = db.exec(select(Module).where(Module.title == "Users & Permissions")).first()
        if restricted_module:
            requested_ids.discard(restricted_module.id)
        db_user.modules = _resolve_m2m_ids(db, Module, list(requested_ids), "Modules")

    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def update_user(*, db: Session, db_user: User, user_update: UserUpdate, updated_by: str) -> User:
    if user_update.username and user_update.username != db_user.username:
        if db.exec(select(User).where(User.username == user_update.username)).first():
            raise DuplicateResourceError("Username already exists")

    data = user_update.model_dump(exclude_unset=True, exclude={"roles", "modules", "skills", "pw"})
    data["last_modified_by"] = updated_by
    db_user.sqlmodel_update(data)

    # Optional password change: an empty/omitted pw keeps the current password.
    if user_update.pw:
        db_user.hashed_pw = get_password_hash(user_update.pw)

    if user_update.roles is not None:
        db_user.roles = _resolve_m2m_ids(db, UserRole, user_update.roles, "Roles")
        
    if user_update.skills is not None:
        db_user.skills = _resolve_m2m_ids(db, UserSkill, user_update.skills, "Skills")

    new_usertype = data.get("usertype", db_user.usertype)
    if new_usertype == UserType.superadmin:
        db_user.modules = db.exec(select(Module)).all()
    elif user_update.modules is not None:
        db_user.modules = _resolve_m2m_ids(db, Module, user_update.modules, "Modules")

    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def delete_user(*, db: Session, username: str, protect_superadmin: bool = True) -> bool:
    statement = select(User).where(User.username == username)
    user = db.exec(statement).first()

    if not user:
        raise ResourceNotFoundError(f"User with username={username} not found")

    if protect_superadmin and user.usertype == UserType.superadmin:
        raise ActionForbiddenError("Deleting a superadmin is not allowed")

    db.delete(user)
    db.commit()
    return True
# endregion


# region Module CRUD
def read_all_modules(db: Session) -> list[ModuleRead]:
    statement = select(Module).options(selectinload(Module.users))
    return db.exec(statement).all()
# endregion