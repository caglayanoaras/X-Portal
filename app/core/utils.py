from fastapi.responses import RedirectResponse
from fastapi import Request
import re

def flash(request: Request, message: str, category: str = "info"):
    """Add a flash message to the session"""
    
    # Grab the existing flashes (or an empty list)
    flashes = request.session.get("_flashes", [])
    
    # Reassign a completely new list (combining the old items with the new one)
    # This forces Starlette to recognize that the session dictionary has been modified.
    request.session["_flashes"] = flashes + [{"message": message, "category": category}]

def get_flashed_messages(request: Request) -> list[dict[str, object]]:
    """Get and clear flash messages from session"""
    flashes = request.session.get("_flashes", [])
    if flashes:
        request.session["_flashes"] = []  # Clear after reading
    return flashes

def redirect_to_route(request: Request, route_name: str, status_code: int = 303):
    """Helper function to redirect to a named route"""
    return RedirectResponse(request.url_for(route_name), status_code=status_code)

def is_valid_email(email: str) -> bool:
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None