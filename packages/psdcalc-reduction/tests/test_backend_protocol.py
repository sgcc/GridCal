"""Backend capability gate tests."""

import pytest

from psdcalc.reduction.backends.base import BackendCapabilities, require_capability
from psdcalc.reduction.exceptions import BackendCapabilityError


def test_transformer_quotient_requires_declared_capability() -> None:
    capabilities = BackendCapabilities(public_spatial_clustering=True)

    with pytest.raises(BackendCapabilityError, match="transformer_quotient"):
        require_capability(capabilities, "transformer_quotient")


def test_declared_capability_passes_gate() -> None:
    capabilities = BackendCapabilities(public_spatial_clustering=True)

    require_capability(capabilities, "public_spatial_clustering")
