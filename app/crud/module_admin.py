"""Generic 'which roles administer this module' mapping.

Reused by every module: a superadmin picks existing roles that may open a
module's admin area. Nothing is stored on the role itself.
"""
from sqlmodel import Session, select

from app.core.exceptions import ResourceNotFoundError
from app.models import Module, UserRole, ModuleAdminRoleLink, ModuleKey


def _module(db: Session, module_key: ModuleKey) -> Module:
    module = db.exec(select(Module).where(Module.key == module_key.value)).first()
    if not module:
        raise ResourceNotFoundError(f"Module '{module_key.value}' not found")
    return module


def list_all_roles(db: Session) -> list[UserRole]:
    return db.exec(select(UserRole).order_by(UserRole.rolename)).all()


def get_admin_role_ids(db: Session, module_key: ModuleKey) -> list[int]:
    module = db.exec(select(Module).where(Module.key == module_key.value)).first()
    if not module:
        return []
    return list(db.exec(
        select(ModuleAdminRoleLink.role_id).where(ModuleAdminRoleLink.module_id == module.id)
    ).all())


def set_admin_roles(*, db: Session, module_key: ModuleKey, role_ids: list[int]) -> list[int]:
    """Replace the module's admin-role set. Unknown role ids are ignored."""
    module = _module(db, module_key)
    valid_ids = set()
    if role_ids:
        valid_ids = {r.id for r in db.exec(select(UserRole).where(UserRole.id.in_(role_ids))).all()}
    for link in db.exec(select(ModuleAdminRoleLink).where(ModuleAdminRoleLink.module_id == module.id)).all():
        db.delete(link)
    for rid in valid_ids:
        db.add(ModuleAdminRoleLink(module_id=module.id, role_id=rid))
    db.commit()
    return sorted(valid_ids)
