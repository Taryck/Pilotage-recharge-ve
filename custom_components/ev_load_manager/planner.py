"""Pure, testable load-balancing decisions. All values are amperes.

The planner computes a complete set of targets using virtual 1 A steps,
then the controller sends one command per changed charger in parallel.
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
                    and c.setpoint - c.minimum >= 1 - EPS
                    and c.largest_draw(hot) > EPS]
        possible.sort(key=lambda c: (-c.largest_draw(hot), c.identifier))
        if possible:
            c = possible[0]
            return Decision("reduce", c.identifier,
                            round(c.setpoint - 1, 3),
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
    # Spare current matters on the phases actually used by each charger.
    # Another phase may have less than 2 A free without blocking recovery.
    eligible = [c for c in chargers if c.active and c.original is not None
                and c.setpoint is not None
                and c.setpoint + EPS < min(c.original, c.maximum)
                and all(v is not None for v in c.currents.values())]
    eligible = [c for c in eligible if c.phases() and all(
        float(limits[p]) - float(totals[p]) >= recovery_margin - EPS
        for p in c.phases())]
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


@dataclass(frozen=True)
class ReductionPlan:
    """Full target set after simulating all 1 A steps with re-ranking."""

    targets: Mapping[str, float]
    steps: tuple[tuple[str, str, float, float], ...]
    remaining: Mapping[str, float]


def plan_reductions(
    totals: Mapping[str, float | None],
    limits: Mapping[str, float],
    chargers: list[Charger],
    *,
    reserved: Mapping[str, float] | None = None,
    unavailable_ids: frozenset[str] = frozenset(),
) -> ReductionPlan:
    """Compute the *whole* shedding plan before sending any HA service call.

    Rank by highest PROJECTED amperage on an overloaded phase. At equal draw,
    prefer the charger closest to the ORIGINAL setpoint. Use its current setpoint
    as original when no snapshot exists. Lower one virtual amp, recompute the
    entire ranking and repeat, then return aggregate final setpoints.

    One reduced charging amp is assumed to free one amp on EVERY active phase,
    including for three-phase chargers; this is a *plan*, not a physical ack.
    A caller can pass reductions already dispatched but not yet reflected in
    meter readings in reserved to avoid sending unnecessary extra reductions.
    """
    over = excesses(totals, limits)
    if any(v is None for v in over.values()):
        return ReductionPlan({}, (), {p: 0.0 for p in PHASES})
    remaining = {p: float(over[p]) for p in PHASES}
    reserved = reserved or {}
    projected = {}
    for c in chargers:
        if c.setpoint is None or not c.active:
            continue
        already = max(0.0, float(reserved.get(c.identifier, 0.0)))
        projected[c.identifier] = float(c.setpoint) - already
        for p in c.phases():
            remaining[p] = max(0.0, remaining[p] - already)

    targets: dict[str, float] = {}
    steps: list[tuple[str, str, float, float]] = []
    while any(v > EPS for v in remaining.values()):
        hot = {p for p, v in remaining.items() if v > EPS}
        eligible = []
        for c in chargers:
            if c.identifier in unavailable_ids or not c.active or c.setpoint is None:
                continue
            if any(v is None for v in c.currents.values()):
                continue
            here = projected[c.identifier]
            if here - c.minimum < 1 - EPS:
                continue
            relevant = c.phases()
            if not hot.intersection(relevant):
                continue
            # Measurements can differ from the setpoint by <1 A and include
            # physical ramping. Calculate projected *measured* draw.
            draw = max(max(0.0, (c.currents[p] or 0.0)
                               - (c.setpoint - here))
                       for p in relevant if p in hot)
            distance_from_original = max(0.0, (
                c.original if c.original is not None else c.setpoint
            ) - here)
            eligible.append((-draw, distance_from_original, c.identifier, c))
        if not eligible:
            break
        eligible.sort(key=lambda item: (item[0], item[1], item[2]))
        selected = eligible[0][3]
        before = projected[selected.identifier]
        after = round(before - 1.0, 3)
        projected[selected.identifier] = after
        targets[selected.identifier] = after
        for p in selected.phases():
            remaining[p] = max(0.0, remaining[p] - 1.0)
        steps.append((selected.identifier, ",".join(sorted(hot)), before, after))
    return ReductionPlan(targets, tuple(steps), remaining)
