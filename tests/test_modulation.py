import pytest

from mpclab.modulation import ModRoute, ModulationMatrix, ParameterSpec


def test_bipolar_route_modulates_normalized_parameter_span():
    matrix = ModulationMatrix([ParameterSpec("gain", 0.0, 2.0, 1.0)])
    matrix.connect("lfo", "gain", 0.25)
    assert matrix.evaluate({}, {"lfo": 1.0})["gain"] == pytest.approx(1.5)
    assert matrix.evaluate({}, {"lfo": -1.0})["gain"] == pytest.approx(0.5)


def test_routes_are_additive_clamped_and_deterministic():
    matrix = ModulationMatrix([ParameterSpec("pan", -1.0, 1.0, 0.0)])
    matrix.connect("a", "pan", 0.4)
    matrix.connect("b", "pan", 0.8, curve="cube")
    assert matrix.evaluate({}, {"a": 1.0, "b": 1.0})["pan"] == 1.0
    assert matrix.serialize() == [
        ModRoute("a", "pan", 0.4).to_dict(),
        ModRoute("b", "pan", 0.8, curve="cube").to_dict(),
    ]


def test_log_parameter_round_trip_and_non_modulatable_guard():
    cutoff = ParameterSpec("cutoff", 20.0, 20000.0, 1000.0, scale="log")
    assert cutoff.from_normalized(cutoff.to_normalized(440.0)) == pytest.approx(440.0)
    matrix = ModulationMatrix([ParameterSpec("fixed", 0.0, 1.0, 0.5, modulatable=False)])
    with pytest.raises(ValueError):
        matrix.connect("lfo", "fixed", 0.2)


def test_missing_source_is_zero_and_nonfinite_source_is_ignored():
    matrix = ModulationMatrix([ParameterSpec("gain", 0.0, 1.0, 0.25)])
    matrix.connect("lfo", "gain", 0.5)
    assert matrix.evaluate({}, {})["gain"] == pytest.approx(0.25)
    assert matrix.evaluate({}, {"lfo": float("nan")})["gain"] == pytest.approx(0.25)
