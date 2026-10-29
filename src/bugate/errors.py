"""Stable machine-readable failures shared by all entrypoints."""
class BugateError(Exception):
    def __init__(self, code, message, *, exit_code=2, details=None):
        super().__init__(message)
        self.code, self.message, self.exit_code, self.details = code, message, exit_code, details

    def as_dict(self):
        return {"code": self.code, "message": self.message, "details": self.details}
