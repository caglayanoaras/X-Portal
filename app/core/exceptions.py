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

class ModuleAccessDenied(BaseAppException):
    """Raised when a user tries to reach a module they aren't assigned to."""
    def __init__(self, module_key: str):
        self.module_key = module_key
        super().__init__(f"Access to module '{module_key}' is not permitted")

class InvalidInputError(BaseAppException):
    """Raised when submitted data fails validation (e.g. a required attribute is missing)."""
    pass

class InvalidStateError(BaseAppException):
    """Raised when an operation isn't allowed in the entity's current state
    (e.g. finishing an action that was never started)."""
    pass