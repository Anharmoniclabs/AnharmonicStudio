"""Deterministic control-rate modulation primitives shared by instruments and FX."""

from __future__ import annotations

from dataclasses import dataclass
import math


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    id: str
    minimum: float
    maximum: float
    default: float
    unit: str = ""
    scale: str = "linear"
    smoothing_ms: float = 0.0
    automatable: bool = True
    modulatable: bool = True

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("parameter id must not be empty")
        values = (self.minimum, self.maximum, self.default, self.smoothing_ms)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("parameter values must be finite")
        if self.maximum <= self.minimum:
            raise ValueError("maximum must be greater than minimum")
        if not self.minimum <= self.default <= self.maximum:
            raise ValueError("default must be within the parameter range")
        if self.smoothing_ms < 0:
            raise ValueError("smoothing_ms must be non-negative")
        if self.scale not in ("linear", "log"):
            raise ValueError("scale must be 'linear' or 'log'")
        if self.scale == "log" and self.minimum <= 0:
            raise ValueError("log parameters require a positive minimum")

    def clamp(self, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("parameter value must be finite")
        return _clamp(float(value), self.minimum, self.maximum)

    def to_normalized(self, value: float) -> float:
        value = self.clamp(value)
        if self.scale == "log":
            low = math.log(self.minimum)
            return (math.log(value) - low) / (math.log(self.maximum) - low)
        return (value - self.minimum) / (self.maximum - self.minimum)

    def from_normalized(self, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("normalized value must be finite")
        value = _clamp(float(value), 0.0, 1.0)
        if self.scale == "log":
            low = math.log(self.minimum)
            return math.exp(low + value * (math.log(self.maximum) - low))
        return self.minimum + value * (self.maximum - self.minimum)


@dataclass(frozen=True, slots=True)
class ModRoute:
    source: str
    destination: str
    amount: float
    bipolar: bool = True
    curve: str = "linear"

    def __post_init__(self) -> None:
        if not self.source or not self.destination:
            raise ValueError("source and destination must not be empty")
        if not math.isfinite(self.amount):
            raise ValueError("amount must be finite")
        if self.curve not in ("linear", "square", "cube"):
            raise ValueError("unsupported modulation curve")

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "destination": self.destination,
            "amount": self.amount,
            "bipolar": self.bipolar,
            "curve": self.curve,
        }


class ModulationMatrix:
    """Apply normalized modulation routes in a deterministic insertion order."""

    def __init__(self, parameters: list[ParameterSpec] | tuple[ParameterSpec, ...]):
        self.parameters = {parameter.id: parameter for parameter in parameters}
        if len(self.parameters) != len(parameters):
            raise ValueError("parameter ids must be unique")
        self.routes: list[ModRoute] = []

    def connect(
        self,
        source: str,
        destination: str,
        amount: float,
        *,
        bipolar: bool = True,
        curve: str = "linear",
    ) -> ModRoute:
        parameter = self.parameters.get(destination)
        if parameter is None:
            raise KeyError(destination)
        if not parameter.modulatable:
            raise ValueError(f"parameter is not modulatable: {destination}")
        route = ModRoute(source, destination, float(amount), bipolar, curve)
        self.routes.append(route)
        return route

    def disconnect(self, route: ModRoute) -> None:
        self.routes.remove(route)

    def clear(self) -> None:
        self.routes.clear()

    @staticmethod
    def _shape(value: float, curve: str) -> float:
        if curve == "square":
            return math.copysign(abs(value) ** 2, value)
        if curve == "cube":
            return value**3
        return value

    def evaluate(
        self,
        base_values: dict[str, float] | None,
        sources: dict[str, float],
    ) -> dict[str, float]:
        base_values = {} if base_values is None else base_values
        normalized = {
            parameter.id: parameter.to_normalized(base_values.get(parameter.id, parameter.default))
            for parameter in self.parameters.values()
        }
        for route in self.routes:
            raw = float(sources.get(route.source, 0.0))
            if not math.isfinite(raw):
                continue
            source = _clamp(raw, -1.0 if route.bipolar else 0.0, 1.0)
            normalized[route.destination] += self._shape(source, route.curve) * route.amount
        return {
            parameter.id: parameter.from_normalized(normalized[parameter.id])
            for parameter in self.parameters.values()
        }

    def serialize(self) -> list[dict]:
        return [route.to_dict() for route in self.routes]
