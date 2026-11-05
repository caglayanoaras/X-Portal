"""Who may do what in Atlas.

Data Admins and Superadmins may do everything: build the schema, assign roles
and work with every object. Everyone else is checked against two role lists,
the class's and the object's own (_roles), combined:

    nobody listed   anyone with the Atlas module may create, read, update and
                    delete the object
    roles listed    only users holding a listed role, each as that role's
                    flags in Users & Permissions allow (can_read, can_update, …)

Creating an object checks the class's list alone. Object roles can only add
access: a role the class lists keeps it on every object of the class.
"""
from dataclasses import dataclass

from app.models import UserType

ADMIN_TYPES = frozenset({UserType.superadmin, UserType.dataadmin})
OPERATIONS = ("create", "read", "update", "delete")


def is_atlas_admin(user) -> bool:
    return getattr(user, "usertype", None) in ADMIN_TYPES


@dataclass
class Viewer:
    username: str
    is_admin: bool
    role_ops: dict          # role id -> frozenset of the operations its flags allow

    def roles_for(self, op: str) -> set:
        return {role_id for role_id, ops in self.role_ops.items() if op in ops}


def viewer_for(user) -> Viewer:
    role_ops = {role.id: frozenset(op for op in OPERATIONS if getattr(role, f"can_{op}", False))
                for role in (user.roles or [])}
    return Viewer(username=user.username, is_admin=is_atlas_admin(user), role_ops=role_ops)


def can_create(viewer: Viewer, cls) -> bool:
    if viewer.is_admin or not cls.roles:
        return True
    return bool(set(cls.roles) & viewer.roles_for("create"))


def can_on_row(viewer: Viewer, cls, op: str, row_roles) -> bool:
    """May the viewer do `op` (read/update/delete) on an object with these _roles?"""
    if viewer.is_admin:
        return True
    listed = set(cls.roles) | set(row_roles or [])
    if not listed:
        return True
    return bool(listed & viewer.roles_for(op))


def row_filter(viewer: Viewer, cls, op: str, alias: str = "s") -> tuple[str, list]:
    """The same rule as can_on_row, as an SQL condition on the object row `alias`."""
    if viewer.is_admin:
        return "1", []
    mine = sorted(viewer.roles_for(op))
    if cls.roles and set(cls.roles) & set(mine):
        return "1", []                      # a class role grants it on every object
    holds_listed = (f'EXISTS (SELECT 1 FROM json_each({alias}."_roles") '
                    f'WHERE value IN ({", ".join("?" * len(mine))}))') if mine else None
    if cls.roles:                           # only the object's own roles can still grant it
        return (holds_listed, mine) if mine else ("0", [])
    nobody_listed = f'json_array_length({alias}."_roles") = 0'
    if mine:
        return f"({nobody_listed} OR {holds_listed})", mine
    return nobody_listed, []
