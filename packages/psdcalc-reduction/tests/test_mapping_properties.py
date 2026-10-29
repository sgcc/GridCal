"""Source-ID mapping composition properties."""

from hypothesis import given, strategies as st

from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.mapping.compose import compose_maps
from psdcalc.reduction.mapping.rows import MapEntry


@given(st.booleans(), st.booleans(), st.booleans())
def test_mapping_composition_preserves_absorption(
    first_absorbed: bool, second_absorbed: bool, third_absorbed: bool
) -> None:
    source = StableId("n", "Line", "source")
    middle = StableId("n", "Line", "middle")
    later = StableId("n", "Line", "later")
    target = StableId("n", "Line", "target")
    first = MapEntry(source, None if first_absorbed else middle,
                     "absorbed_internal" if first_absorbed else "aggregated", "one")
    second = MapEntry(middle, None if second_absorbed else later,
                      "absorbed_internal" if second_absorbed else "aggregated", "two")
    third = MapEntry(later, None if third_absorbed else target,
                     "absorbed_internal" if third_absorbed else "aggregated", "three")

    left = compose_maps(compose_maps((first,), (second,)), (third,))[0]
    right = compose_maps((first,), compose_maps((second,), (third,)))[0]

    assert left.source_id == right.source_id == source
    assert left.target_id == right.target_id
    assert (left.mapping_type == "absorbed_internal") == (
        first_absorbed or second_absorbed or third_absorbed
    )
