class BaseAppException(Exception):
    """Base class for all custom application exceptions."""
    def __init__(self, detail: str):
        self.detail = detail

class DuplicateResourceError(BaseAppException):
    """Raised when trying to create a resource that already exists (e.g., unique constraint)."""
    pass

class ResourceNotFoundError(BaseAppException):
    """Raised when a requested resource (like an M2M link ID) does not exist."""
    pass

class ActionForbiddenError(BaseAppException):
    """Raised when an action is not allowed (e.g., deleting a superadmin)."""
    pass