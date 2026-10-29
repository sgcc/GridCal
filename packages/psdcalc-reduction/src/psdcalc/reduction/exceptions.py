"""Domain errors for reduction workflows."""


class ReductionError(Exception):
    """Base class for explicit reduction failures."""


class BackendCapabilityError(ReductionError):
    """A backend lacks a required public capability."""
