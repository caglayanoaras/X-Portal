from enum import Enum
from datetime import datetime, timezone
from sqlalchemy import DateTime

from pydantic import EmailStr
from sqlalchemy import Column, ForeignKey
from sqlmodel import SQLModel, Field, Relationship


# region Link Models
class UserRoleLink(SQLModel, table=True):
    user_id: int = Field(
        sa_column=Column(
            ForeignKey("user.id", ondelete="CASCADE"), 
            primary_key=True
        )
    )
    role_id: int = Field(
        sa_column=Column(
            ForeignKey("userrole.id", ondelete="CASCADE"), 
            primary_key=True
        )
    )


class UserSkillLink(SQLModel, table=True):
    user_id: int = Field(
        sa_column=Column(
            ForeignKey("user.id", ondelete="CASCADE"), 
            primary_key=True
        )
    )
    skill_id: int = Field(
        sa_column=Column(
            ForeignKey("userskill.id", ondelete="CASCADE"), 
            primary_key=True
        )
    )


class UserModuleLink(SQLModel, table=True):
    user_id: int = Field(
        sa_column=Column(
            ForeignKey("user.id", ondelete="CASCADE"), 
            primary_key=True
        )
    )
    module_id: int = Field(
        sa_column=Column(
            ForeignKey("module.id", ondelete="CASCADE"), 
            primary_key=True
        )
    )
# endregion


# region User Models
class UserType(str, Enum):
    superadmin = "Super Admin"
    dataadmin = "Data Admin"
    regular = "Regular"


class UserShortBase(SQLModel):
    username: str = Field(index=True, nullable=False, unique=True)
    name: str
    surname: str
    email: EmailStr
    usertype: UserType


class UserImagelessBase(UserShortBase):
    title: str | None = None
    is_active: bool = Field(default=True)


class UserBase(UserImagelessBase):
    profile_image_path: str | None = Field(default="default_profile_image.png")
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            default=lambda: datetime.now(timezone.utc),
            nullable=False
        )
    )
    created_by: str
    last_modified_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            default=lambda: datetime.now(timezone.utc),
            onupdate=lambda: datetime.now(timezone.utc),
            nullable=False
        )
    )
    last_modified_by: str


class User(UserBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    hashed_pw: str
    
    roles: list["UserRole"] = Relationship(
        back_populates="users", 
        link_model=UserRoleLink
    )
    skills: list["UserSkill"] = Relationship(
        back_populates="users", 
        link_model=UserSkillLink
    )
    modules: list["Module"] = Relationship(
        back_populates="users", 
        link_model=UserModuleLink
    )


class UserCreate(UserImagelessBase):
    pw: str
    roles: list[int] = Field(default_factory=list, description="List of role IDs")
    modules: list[int] = Field(default_factory=list, description="List of module IDs")
    skills: list[int] = Field(default_factory=list, description="List of skill IDs")


class UserRead(UserImagelessBase):
    id: int
    roles: list["UserRoleShortRead"] | None = None
    skills: list["UserSkillShortRead"] | None = None
    modules: list["ModuleShortRead"] | None = None


class UserShortRead(UserShortBase):
    id: int


class UserUpdate(SQLModel):
    username: str | None = Field(default=None)
    email: EmailStr | None = Field(default=None)
    name: str | None = Field(default=None)
    surname: str | None = Field(default=None)
    title: str | None = Field(default=None)
    usertype: UserType | None = Field(default=None)
    is_active: bool | None = Field(default=None)
    roles: list[int] | None = Field(default=None, description="List of role IDs")
    modules: list[int] | None = Field(default=None, description="List of module IDs")
    skills: list[int] | None = Field(default=None, description="List of skill IDs")
# endregion


# region UserRole Models
class UserRoleBase(SQLModel):
    rolename: str = Field(index=True, unique=True, nullable=False)
    can_create: bool = Field(default=False)
    can_read: bool = Field(default=False)
    can_update: bool = Field(default=False)
    can_delete: bool = Field(default=False)
    notes: str | None = Field(default=None, max_length=255)


class UserRole(UserRoleBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    users: list["User"] = Relationship(
        back_populates="roles",
        link_model=UserRoleLink
    )


class UserRoleCreate(UserRoleBase):
    pass


class UserRoleRead(UserRoleBase):
    id: int
    users: list["UserShortRead"] | None = None


class UserRoleShortRead(UserRoleBase):
    id: int


class UserRoleUpdate(UserRoleBase):
    rolename: str | None = Field(default=None)
    can_create: bool | None = Field(default=None)
    can_read: bool | None = Field(default=None)
    can_update: bool | None = Field(default=None)
    can_delete: bool | None = Field(default=None)
    notes: str | None = Field(default=None, max_length=255)
# endregion


# region UserSkill Models    
class UserSkillBase(SQLModel):
    skillname: str = Field(index=True, unique=True, nullable=False)
    skill_level: int = Field(default=0)
    notes: str | None = Field(default=None, max_length=255)


class UserSkill(UserSkillBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    users: list["User"] = Relationship(
        back_populates="skills",
        link_model=UserSkillLink
    )


class UserSkillCreate(UserSkillBase):
    pass


class UserSkillRead(UserSkillBase):
    id: int
    users: list["UserShortRead"] | None = None


class UserSkillShortRead(UserSkillBase):
    id: int


class UserSkillUpdate(UserSkillBase):
    skillname: str | None = Field(default=None)
    skill_level: int | None = Field(default=None)
    notes: str | None = Field(default=None, max_length=255)
# endregion


# region Module Models
class ModuleBase(SQLModel):
    title: str
    description: str


class Module(ModuleBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    linkname: str
    image_url: str
    users: list["User"] = Relationship(
        back_populates="modules",
        link_model=UserModuleLink
    )


class ModuleShortRead(ModuleBase):
    id: int


class ModuleRead(ModuleBase):
    id: int
    users: list["UserShortRead"] | None = None
# endregion