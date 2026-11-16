"""Pure Python parking and EV charger simulator, no Home Assistant dependency."""
from __future__ import annotations

from dataclasses import dataclass, field
import random

PHASES = ("l1", "l2", "l3")
MODES = ("tri", "l1", "l2", "l3")


@dataclass
class Charger:
    index: int
    mode: str = "tri"
    initial_current: float = 16.0
    commanded_current: float = 16.0
    physical_current: float = 0.0
    hardware_min: float = 8.0
    hardware_max: float = 32.0
    target_kwh: float = 25.0
    delivered_kwh: float = 0.0
    initial_for_session: float | None = None
    status: str = "stopped"
    stop_remaining: float = 0.0
    stop_from: float = 0.0
    jitter: dict[str, float] = field(default_factory=lambda: dict.fromkeys(PHASES, 0.0))
    ignore_commands: bool = False

    def active_phases(self) -> tuple[str, ...]:
        return PHASES if self.mode == "tri" else (self.mode,)

    def start(self) -> bool:
        """Begin a cycle; the configured starting current never changes."""
        if not self.hardware_min <= self.initial_current <= self.hardware_max:
            return False
        if self.target_kwh <= 0:
            return False
        self.initial_for_session = self.initial_current
        self.commanded_current = self.initial_current
        self.delivered_kwh = 0.0
        self.status = "charging"
        self.physical_current = 0.0
        self.stop_remaining = 0.0
        return True

    def command(self, current: float) -> bool:
        """Reject bad settings without changing current commanded or measured."""
        if self.ignore_commands or not self.hardware_min <= current <= self.hardware_max:
            return False
        self.commanded_current = current
        return True

    def stop(self) -> None:
        if self.status not in ("charging", "stopping"):
            return
        if self.status != "stopping":
            self.stop_from = self.physical_current
            self.stop_remaining = 2.0
        self.status = "stopping"

    def tick(self, dt: float, factor: float, rng: random.Random) -> None:
        """Measured charging current ramps by random 1..2 A/s; stop takes 2s."""
        if dt <= 0:
            return
        if self.status == "charging":
            delta = rng.uniform(1.0, 2.0) * dt
            target = self.commanded_current
            if self.physical_current < target:
                self.physical_current = min(target, self.physical_current + delta)
            else:
                self.physical_current = max(target, self.physical_current - delta)
        elif self.status == "stopping":
            self.stop_remaining = max(0.0, self.stop_remaining - dt)
            self.physical_current = self.stop_from * self.stop_remaining / 2
            if self.stop_remaining <= 0:
                self.status = "stopped"
                self.physical_current = 0.0
        else:
            self.physical_current = 0.0

        for phase in PHASES:
            self.jitter[phase] = rng.uniform(-0.2, 0.2)
        # Only the energy calculation uses the time multiplier.
        # Voltage 230 V per phase, cos phi = 1.
        if self.status == "charging":
            amperes = sum(self.currents().values())
            self.delivered_kwh += 230 * amperes * dt * factor / 3_600_000
            if self.delivered_kwh >= self.target_kwh:
                self.delivered_kwh = self.target_kwh
                self.stop()

    def currents(self) -> dict[str, float]:
        return {
            phase: (round(max(0.0, self.physical_current + self.jitter[phase]), 3)
                    if phase in self.active_phases() and self.physical_current > 0 else 0.0)
            for phase in PHASES
        }


@dataclass
class Parking:
    count: int = 10
    factor: float = 5.0
    thresholds: dict[str, float] = field(
        default_factory=lambda: dict.fromkeys(PHASES, 50.0)
    )
    trip_seconds: float = 15.0
    seed: int = 12345
    chargers: list[Charger] = field(init=False)
    tripped: bool = False
    seconds_over_limit: float = 0.0
    elapsed: float = 0.0
    rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not 1 <= self.count <= 100:
            raise ValueError("Charger count must be 1..100")
        self.chargers = [Charger(i) for i in range(1, self.count + 1)]
        self.rng = random.Random(self.seed)

    def totals(self) -> dict[str, float]:
        currents = [charger.currents() for charger in self.chargers]
        return {p: round(sum(c[p] for c in currents), 3) for p in PHASES}

    def excess(self) -> dict[str, float]:
        totals = self.totals()
        return {p: round(max(0.0, totals[p] - self.thresholds[p]), 3) for p in PHASES}

    def tick(self, dt: float) -> None:
        if dt <= 0:
            return
        self.elapsed += dt
        for charger in self.chargers:
            charger.tick(dt, self.factor, self.rng)
        if self.tripped:
            for charger in self.chargers:
                charger.stop()
            return
        if any(value > 0 for value in self.excess().values()):
            self.seconds_over_limit += dt
            if self.seconds_over_limit >= self.trip_seconds:
                self.tripped = True
                for charger in self.chargers:
                    charger.stop()
        else:
            self.seconds_over_limit = 0.0

    def reset_trip(self) -> None:
        self.tripped = False
        self.seconds_over_limit = 0.0
