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


class IdentityError(ReductionError):
    """A stable identity or sidecar binding is invalid."""


class SignatureError(ReductionError):
    """A grid signature cannot be computed from the supplied input."""


class BackendIOError(ReductionError):
    """A backend import or export operation failed."""


class RoundTripError(ReductionError):
    """An exported network could not be restored with stable identity."""


class ProtectionError(ReductionError):
    """A protection selector or action cannot be applied safely."""


class PartitionError(ReductionError):
    """A busmap is incomplete or violates hard topology constraints."""
