import os

import typer
from sqlmodel import select

from app.core.database import get_sync_session, init_db
from app.models import User, UserType, Module
from app.dependencies.auth import get_password_hash


cli = typer.Typer()


@cli.command()
def create_superadmin(
    username: str = typer.Option(..., prompt=True),
    email: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True),
    name: str = typer.Option(..., prompt=True),
    surname: str = typer.Option(..., prompt=True),
    title: str = typer.Option(..., prompt=True),
):
    """Add a new SuperAdmin user."""

    init_db()  # Ensure tables exist
    hashed_pw = get_password_hash(password)

    with get_sync_session() as session:
        existing_email = session.exec(
            select(User).where(User.email == email)
        ).first()

        existing_username = session.exec(
            select(User).where(User.username == username)
        ).first()

        if existing_username:
            typer.secho(
                "A user with this username already exists.",
                fg=typer.colors.RED
            )
            return

        if existing_email:
            typer.secho(
                "A user with this email already exists.",
                fg=typer.colors.RED
            )
            return

        default_image_path = os.path.join('default_profile_image.png')

        user = User(
            username=username,
            email=email,
            hashed_pw=hashed_pw,
            usertype=UserType.superadmin,
            name=name,
            surname=surname,
            title=title,
            profile_image_path=default_image_path,
            created_by='root',
            last_modified_by='root'
        )
        user.modules = session.exec(select(Module)).all()
        session.add(user)
        session.commit()
        session.refresh(user)

        typer.secho(
            f"SuperAdmin '{username}: {email}' created successfully.", 
            fg=typer.colors.GREEN
        )


if __name__ == "__main__":
    cli()