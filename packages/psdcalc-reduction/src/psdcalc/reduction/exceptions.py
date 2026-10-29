"""Domain errors for reduction workflows."""


class ReductionError(Exception):
    """Base class for explicit reduction failures."""


class BackendCapabilityError(ReductionError):
    """A backend lacks a required public capability."""


class ConfigSchemaError(ReductionError):
    """Configuration syntax, type, version, or unknown field is invalid."""


class UnsupportedFeatureError(ReductionError):
    """A recognized future capability was enabled before implementation."""


class ConfigSemanticError(ReductionError):
    """A well-formed configuration has inconsistent values."""


class ArtifactError(ReductionError):
    """An artifact path or persistent write is invalid."""
