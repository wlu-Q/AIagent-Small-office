"""Codes for Office errors shown by the desktop client."""

class _OfficeErrorDetail:
    def __init__(self, message: str, *, code: str, params: dict | None = None):
        super().__init__(message)
        self.public_code = code
        self.public_params = params or {}


class OfficeInputError(_OfficeErrorDetail, ValueError):
    pass


class OfficeBusyError(_OfficeErrorDetail, RuntimeError):
    pass


class OfficeNotFoundError(_OfficeErrorDetail, FileNotFoundError):
    pass
