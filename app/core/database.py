from sqlmodel import SQLModel, create_engine, Session, select
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models import *

engine = create_engine(settings.DATABASE_URL, echo=False)


# SQLite disables foreign-key enforcement per connection by default, so
# `ondelete="CASCADE"` on our link tables is ignored unless we turn it on for
# every connection the engine hands out.
if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# Dependency for FastAPI routes
def get_session():
    with Session(engine) as session:
        yield session

# For scripts like create_superadmin.py
def get_sync_session():
    return Session(engine)

"""
HOW TO ADD MODULES:
linkname is the name of the path operation of the index page of the module.
for each module we should have a router under app\\api directory. 
for each module we might have a crud function module under app\\crud directory.

"""
def init_db():
    SQLModel.metadata.create_all(engine)
    initial_modules = [
        {
            "key": "users_and_permissions",
            "title": "Users & Permissions",
            "description": "Management of user credentials",
            "linkname": "users_and_permissions_index",
            "image_url": "/static/images/users_and_permissions.png"
        },
        {
            "key": "atlas",
            "title": "The Atlas",
            "description": "Centralized management of laboratory data, records, and reference tables.",
            "linkname": "atlas_index",
            "image_url": "/static/images/atlas.png"
        },
        {
            "key": "actions",
            "title": "Actions",
            "description": "Trigger, assign, and track operational actions across the platform.",
            "linkname": "actions_index",
            "image_url": "/static/images/actions.png"
        },
        {
            "key": "data_panels",
            "title": "Data Panels",
            "description": "Configurable data panels and dashboards for at-a-glance insights.",
            "linkname": "data_panels_index",
            "image_url": "/static/images/data_panels.png"
        },

    ]
 
    with Session(engine) as session:
        existing_modules = session.exec(select(Module)).all()
        by_linkname      = {m.linkname: m for m in existing_modules}

        new_or_updated   = False
        for data in initial_modules:
            link = data["linkname"]

            if link not in by_linkname:
                # ‼️ Brand‑new module → insert
                session.add(Module(**data))
                new_or_updated = True
            else:
                # Optional “up‑sert”: keep row but sync title / description / image
                row           = by_linkname[link]
                fields_to_chk = ("title", "description", "image_url")
                if any(getattr(row, f) != data[f] for f in fields_to_chk):
                    for f in fields_to_chk:
                        setattr(row, f, data[f])
                    new_or_updated = True

        if new_or_updated:
            session.commit()    # commit only if something changed
            for module in session.exec(select(Module)).all():
                session.refresh(module)  # ensures the session knows fresh PKs

        all_modules  = session.exec(select(Module)).all()
        superadmins  = session.exec(
            select(User).where(User.usertype == UserType.superadmin)
        ).all()

        for user in superadmins:
            owned = {m.id for m in user.modules}
            for mod in all_modules:
                if mod.id not in owned:
                    user.modules.append(mod)   # creates the link row
                    new_or_updated = True

        if new_or_updated:
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
