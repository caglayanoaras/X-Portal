class AtlasError(Exception):
    """An error the API returns as {"detail": ..., **extra} with this status.

    `extra` carries machine-readable details, e.g. warnings=[...] and
    requires_force=True when a schema change would delete data.
    """

    def __init__(self, status: int, detail: str, **extra):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.extra = extra
