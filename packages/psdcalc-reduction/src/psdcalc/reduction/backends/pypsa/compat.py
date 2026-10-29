"""Single boundary for PyPSA's public spatial clustering accessor.

These functions receive a working network copy. They never alter the original
network or fall back to PyPSA private modules.
"""

from collections.abc import Mapping
from typing import Any, Callable

from psdcalc.reduction.exceptions import BackendCapabilityError


def _spatial_method(network: object, name: str) -> Callable[..., Any]:
    """Return a public spatial method or raise a capability error."""
    cluster = getattr(network, "cluster", None)
    spatial = getattr(cluster, "spatial", None)
    if spatial is None:
        raise BackendCapabilityError("PyPSA network has no public cluster.spatial accessor")
    method = getattr(spatial, name, None)
    if not callable(method):
        raise BackendCapabilityError(f"PyPSA cluster.spatial has no public {name} method")
    return method


def busmap_by_kmeans(
    network: object,
    bus_weightings: object,
    n_clusters: int,
    seed: int,
) -> Any:
    """Build a busmap through PyPSA's public K-means accessor with a seed."""
    return _spatial_method(network, "busmap_by_kmeans")(
        bus_weightings=bus_weightings,
        n_clusters=n_clusters,
        random_state=seed,
        n_init=10,
    )


def busmap_by_hac(network: object, n_clusters: int, linkage: str = "ward") -> Any:
    """Build a busmap through PyPSA's public HAC accessor."""
    return _spatial_method(network, "busmap_by_hac")(
        n_clusters=n_clusters, linkage=linkage
    )


def busmap_by_greedy_modularity(network: object, n_clusters: int) -> Any:
    """Build a busmap through PyPSA's public modularity accessor."""
    return _spatial_method(network, "busmap_by_greedy_modularity")(
        n_clusters=n_clusters
    )


def get_clustering_from_busmap(
    network: object,
    busmap: Mapping[str, str],
    *,
    options: Mapping[str, Any] | None = None,
) -> Any:
    """Materialize a quotient through PyPSA's public spatial accessor."""
    return _spatial_method(network, "get_clustering_from_busmap")(
        dict(busmap), **dict(options or {})
    )
