"""Library input errors carry display codes without importing the Agent runtime."""

class LibraryInputError(ValueError):
    def __init__(self, message: str, *, code: str, params: dict | None = None):
        super().__init__(message)
        self.public_code = code
        self.public_params = params or {}
