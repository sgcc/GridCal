"""Contract tests for the public PyPSA clustering boundary."""

from types import MappingProxyType, SimpleNamespace

import pytest

from psdcalc.reduction.backends.pypsa.compat import (
    busmap_by_greedy_modularity,
    busmap_by_hac,
    busmap_by_kmeans,
    get_clustering_from_busmap,
)
from psdcalc.reduction.exceptions import BackendCapabilityError


class RecordingSpatial:
    """Record calls made to the public spatial accessor."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def busmap_by_kmeans(self, *args: object, **kwargs: object) -> str:
        self.calls.append(("kmeans", args, kwargs))
        return "kmeans-map"

    def busmap_by_hac(self, *args: object, **kwargs: object) -> str:
        self.calls.append(("hac", args, kwargs))
        return "hac-map"

    def busmap_by_greedy_modularity(self, *args: object, **kwargs: object) -> str:
        self.calls.append(("modularity", args, kwargs))
        return "modularity-map"

    def get_clustering_from_busmap(self, *args: object, **kwargs: object) -> str:
        self.calls.append(("quotient", args, kwargs))
        return "clustering-result"


def test_kmeans_uses_public_accessor_and_explicit_seed() -> None:
    spatial = RecordingSpatial()
    network = SimpleNamespace(cluster=SimpleNamespace(spatial=spatial))

    result = busmap_by_kmeans(network, bus_weightings={"b1": 1}, n_clusters=2, seed=42)

    assert result == "kmeans-map"
    assert spatial.calls == [
        ("kmeans", (), {"bus_weightings": {"b1": 1}, "n_clusters": 2,
                         "random_state": 42, "n_init": 10})
    ]


def test_other_public_clustering_methods_are_forwarded() -> None:
    spatial = RecordingSpatial()
    network = SimpleNamespace(cluster=SimpleNamespace(spatial=spatial))

    assert busmap_by_hac(network, n_clusters=3, linkage="ward") == "hac-map"
    assert busmap_by_greedy_modularity(network, n_clusters=3) == "modularity-map"
    assert get_clustering_from_busmap(network, {"b1": "c1"},
                                     options={"line_length_factor": 1.0}) == "clustering-result"
    assert [call[0] for call in spatial.calls] == ["hac", "modularity", "quotient"]


def test_missing_public_accessor_is_an_explicit_capability_error() -> None:
    network = SimpleNamespace(clustering=SimpleNamespace())

    with pytest.raises(BackendCapabilityError, match="cluster.spatial"):
        busmap_by_greedy_modularity(network, n_clusters=2)


def test_missing_public_method_is_an_explicit_capability_error() -> None:
    network = SimpleNamespace(cluster=SimpleNamespace(spatial=SimpleNamespace()))

    with pytest.raises(BackendCapabilityError, match="busmap_by_hac"):
        busmap_by_hac(network, n_clusters=2)


def test_quotient_converts_read_only_busmap_to_public_api_dict() -> None:
    spatial = RecordingSpatial()
    network = SimpleNamespace(cluster=SimpleNamespace(spatial=spatial))

    get_clustering_from_busmap(network, MappingProxyType({"b1": "c1"}))

    assert type(spatial.calls[0][1][0]) is dict
