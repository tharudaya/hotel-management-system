import itertools

import pytest

from app.models import HousekeepingStatus
from app.services.errors import InvalidTransitionError
from app.services.rooms import HOUSEKEEPING_TRANSITIONS, validate_housekeeping_transition

HK = HousekeepingStatus

# Written out independently of the implementation, so a change to the table
# has to be made deliberately in both places.
EXPECTED_ALLOWED: set[tuple[HousekeepingStatus, HousekeepingStatus]] = {
    (HK.DIRTY, HK.IN_PROGRESS),
    (HK.DIRTY, HK.CLEAN),
    (HK.DIRTY, HK.OUT_OF_ORDER),
    (HK.IN_PROGRESS, HK.CLEAN),
    (HK.IN_PROGRESS, HK.DIRTY),
    (HK.IN_PROGRESS, HK.OUT_OF_ORDER),
    (HK.CLEAN, HK.INSPECTED),
    (HK.CLEAN, HK.DIRTY),
    (HK.CLEAN, HK.OUT_OF_ORDER),
    (HK.INSPECTED, HK.DIRTY),
    (HK.INSPECTED, HK.OUT_OF_ORDER),
    (HK.OUT_OF_ORDER, HK.DIRTY),
}
ALL_PAIRS: list[tuple[HousekeepingStatus, HousekeepingStatus]] = list(
    itertools.product(HousekeepingStatus, HousekeepingStatus)
)


def test_every_status_has_an_entry() -> None:
    assert set(HOUSEKEEPING_TRANSITIONS) == set(HousekeepingStatus)


@pytest.mark.parametrize(("current", "target"), sorted(EXPECTED_ALLOWED))
def test_allowed_transitions(current: HousekeepingStatus, target: HousekeepingStatus) -> None:
    validate_housekeeping_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"), [pair for pair in ALL_PAIRS if pair not in EXPECTED_ALLOWED]
)
def test_disallowed_transitions(current: HousekeepingStatus, target: HousekeepingStatus) -> None:
    with pytest.raises(InvalidTransitionError) as exc_info:
        validate_housekeeping_transition(current, target)

    assert exc_info.value.code == "invalid_transition"
    assert f"from {current.value} to {target.value}" in exc_info.value.message


def test_error_message_lists_allowed_targets() -> None:
    with pytest.raises(InvalidTransitionError) as exc_info:
        validate_housekeeping_transition(HK.OUT_OF_ORDER, HK.CLEAN)

    assert exc_info.value.message == (
        "Cannot change housekeeping status from out_of_order to clean. "
        "Allowed from out_of_order: dirty."
    )
