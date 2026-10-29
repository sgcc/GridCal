"""Public PyPSA energy planner wrappers with deterministic source IDs."""

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import FeatureBuilder, PartitionOptions
from psdcalc.reduction.planners.energy import EnergyPartitionPlanner
from psdcalc.reduction.protection.engine import ProtectionEngine


@pytest.mark.parametrize("planner", ["pypsa_kmeans", "pypsa_hac", "pypsa_modularity"])
def test_energy_planners_return_source_id_partition(planner: str) -> None:
    network = pypsa.Network()
    for index in range(4):
        network.add("Bus", f"b{index}", v_nom=110, x=float(index), y=0.0)
        if index:
            network.add("Line", f"l{index}", bus0=f"b{index-1}",
                        bus1=f"b{index}", r=0.01, x=0.1, s_nom=100)
    network.add("Load", "demand", bus="b0", p_set=5)
    network.add("Generator", "gen", bus="b3", p_nom=10)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    before = adapter.topology_signature(network, sidecar)

    result = EnergyPartitionPlanner().plan(
        network, sidecar, protection, planner=planner, n_clusters=2,
        seed=17, builders=[FeatureBuilder(type="load_generation")],
        options=PartitionOptions(n_init=10), connectivity_policy="split_disconnected",
    )

    assert len(result.stable_busmap) == 4
    assert result.requested_cluster_count == 2
    assert result.actual_cluster_count >= 2
    assert adapter.topology_signature(network, sidecar) == before
