"""Pure, testable load-balancing decisions. All values are amperes.

The planner emits at most ONE one-ampere adjustment. The HA adapter must
observe acknowledgement before requesting another decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

PHASES = ("l1", "l2", "l3")
EPS = 0.00001


@dataclass(frozen=True)
class Charger:
    """A charger with verified readings and a known setpoint."""

    identifier: str
    currents: Mapping[str, float | None]
    setpoint: float | None
    minimum: float
    maximum: float
    original: float | None = None
    active: bool = True

    def phases(self) -> tuple[str, ...]:
        """Phases where this charger demonstrably draws current."""
        return tuple(p for p in PHASES if self.currents.get(p) is not None
                     and self.currents[p] > EPS)

    def largest_draw(self, phases: set[str]) -> float:
        return max((self.currents[p] or 0 for p in self.phases() if p in phases),
                   default=0.0)


@dataclass(frozen=True)
class Decision:
    action: str  # reduce, restore, stop, or idle
    charger_id: str | None = None
    target: float | None = None
    reason: str = ""


def excesses(totals: Mapping[str, float | None],
             limits: Mapping[str, float]) -> dict[str, float | None]:
    """Per-phase excess; None means that phase cannot be assessed."""
    return {
        phase: (None if totals.get(phase) is None else
                max(0.0, float(totals[phase]) - float(limits[phase])))
        for phase in PHASES
    }


def decide(totals: Mapping[str, float | None],
           limits: Mapping[str, float],
           chargers: list[Charger],
           *, recovery_margin: float = 2.0) -> Decision:
    """Select next one-ampere action.

    A reduction is selected by largest measured draw on an overloaded phase.
    After every acknowledged step the caller must rerun this algorithm.
    Restoration begins only if each supply phase has >=2 A headroom;
    it prefers smallest current draw, then greatest amount reduced.
    """
    over = excesses(totals, limits)
    if any(value is None for value in over.values()):
        return Decision("idle", reason="Installation current unavailable")
    hot = {phase for phase, value in over.items() if value > EPS}
    if hot:
        # An unmeasurable charger is not assumed to draw zero. Shut it down
        # before attempting to balance an overloaded installation.
        unmeasurable = sorted(
            (c for c in chargers if c.active and
             (c.setpoint is None or any(v is None for v in c.currents.values()))),
            key=lambda c: c.identifier,
        )
        if unmeasurable:
            return Decision("stop", unmeasurable[0].identifier,
                            reason="Charger measurement or setpoint unavailable")
        possible = [c for c in chargers if c.active and c.setpoint is not None
                    and c.setpoint > c.minimum + EPS
                    and c.largest_draw(hot) > EPS]
        possible.sort(key=lambda c: (-c.largest_draw(hot), c.identifier))
        if possible:
            c = possible[0]
            return Decision("reduce", c.identifier,
                            max(c.minimum, round(c.setpoint - 1, 3)),
                            "Exceeded " + ", ".join(sorted(hot)))
        # Current below minimum cannot be used as a control setting.
        # Stop a still-drawing charger only after all reducible candidates
        # on the overloaded phase have reached their minimum.
        stoppable = [c for c in chargers if c.active and c.largest_draw(hot) > EPS]
        stoppable.sort(key=lambda c: (-c.largest_draw(hot), c.identifier))
        if stoppable:
            return Decision("stop", stoppable[0].identifier,
                            reason="Exceeded threshold at charger minimum")
        return Decision("idle", reason="No charger drawing on exceeded phases")

    # Recovery must not compete with load shedding on another phase.
    if any(float(limits[p]) - float(totals[p]) + EPS < recovery_margin
           for p in PHASES):
        return Decision("idle", reason="Insufficient recovery headroom")

    eligible = [c for c in chargers if c.active and c.original is not None
                and c.setpoint is not None
                and c.setpoint + EPS < min(c.original, c.maximum)
                and all(v is not None for v in c.currents.values())]
    # A +1 A change is conservatively assumed to load every configured
    # supply phase by 1 A; retain >=1 A margin on all three phases.
    eligible = [c for c in eligible if all(
        float(limits[p]) - float(totals[p]) >= 2 - EPS for p in PHASES)]
    eligible.sort(key=lambda c: (
        sum(c.currents[p] or 0 for p in PHASES),
        -(c.original - c.setpoint), c.identifier,
    ))
    if eligible:
        c = eligible[0]
        return Decision("restore", c.identifier,
                        min(c.maximum, c.original, round(c.setpoint + 1, 3)),
                        "Recovery headroom")
    return Decision("idle", reason="No adjustment needed")
