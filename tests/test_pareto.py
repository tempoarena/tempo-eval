import pytest

from tempo_eval.pareto import frontier, hypervolume, speed


def test_speed_axis_is_logarithmic():
    assert speed(1) == 1.0 and speed(10_000) == 0.0
    assert speed(100) == pytest.approx(0.5)
    assert speed(0.01) == 1.0 and speed(1e9) == 0.0


def test_frontier_keeps_only_undominated_points():
    pts = [(50, 0.4), (1000, 0.9), (200, 0.3), (5000, 0.8), (40, 0.4)]
    assert frontier(pts) == [4, 1]


def test_hypervolume():
    assert hypervolume([(1, 1.0)]) == pytest.approx(1.0)
    assert hypervolume([(100, 1.0)]) == pytest.approx(0.5)
    # a dominated point adds nothing
    assert hypervolume([(100, 1.0), (1000, 0.5)]) == pytest.approx(0.5)
    # two frontier points: 0.25 * 0.4 + 0.5 * 1.0... fast weak + slow strong
    assert hypervolume([(10, 0.4), (100, 1.0)]) == pytest.approx(0.5 * 1.0 + 0.25 * 0.4)
