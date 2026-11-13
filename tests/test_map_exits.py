"""Exit calibration preserves user points, isolates axes, and never posts game input."""
import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from voix_des_douze.map_exits import ExitCalibration, exit_radius, sample_exit
from voix_des_douze.settings import Store


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def profile():
    return {"size": [1000, 700], "radius": [110, 120],
            "points": {"left": [2, 20], "right": [998, 680],
                       "up": [20, 2], "down": [980, 698]}}


@pytest.mark.parametrize("direction,expected", [
    ("left", ((2, 0), (2, 140))), ("right", ((998, 560), (998, 699))),
    ("up", ((0, 2), (130, 2))), ("down", ((870, 698), (999, 698)))])
def test_random_exit_varies_only_intended_axis_and_stays_on_screen(monkeypatch, direction, expected):
    for boundary, coordinates in zip((min, max), expected):
        monkeypatch.setattr("voix_des_douze.map_exits.random.uniform", lambda a, b: boundary(a, b))
        point = sample_exit(profile(), direction)
        assert (point.x(), point.y()) == coordinates


def test_calibration_requires_four_points_and_cancel_does_not_modify_saved(app, tmp_path):
    screen = app.primaryScreen()
    panel = ExitCalibration(screen)
    results = []
    panel.finished.connect(results.append)
    panel.show()
    QTest.keyClick(panel, Qt.Key_Return)
    assert results == []
    width, height = panel.width(), panel.height()
    points = [(3, height // 4), (width - 4, height // 4),
              (width // 2, 4), (width // 2, height - 5)]
    for point in points:
        QTest.mouseClick(panel, Qt.LeftButton, pos=QPoint(*point))
    QTest.keyClick(panel, Qt.Key_Return)
    assert results[0]["points"] == dict(zip(("left", "right", "up", "down"), map(list, points)))
    store = Store(tmp_path / "settings.json")
    store.settings.map_exits["screen"] = results[0]
    store.settings.map_exit_screen = "screen"
    store.save()
    saved = Store(store.path).settings.map_exits["screen"]
    cancelled = ExitCalibration(screen, saved)
    cancelled.finished.connect(results.append)
    cancelled.show()
    QTest.mouseClick(cancelled.buttons["right"], Qt.LeftButton)
    QTest.mouseClick(cancelled, Qt.LeftButton, pos=QPoint(width - 20, height // 4))
    assert cancelled.points["right"] != saved["points"]["right"]
    QTest.keyClick(cancelled, Qt.Key_Escape)
    assert results[-1] is None
    assert Store(store.path).settings.map_exits["screen"]["points"] == saved["points"]


def test_changed_screen_size_requires_recalibration(app):
    saved = profile()
    saved["size"] = [1, 1]
    panel = ExitCalibration(app.primaryScreen(), saved)
    results = []
    panel.finished.connect(results.append)
    panel.commit()
    assert results == []
    panel.close()


@pytest.mark.parametrize("change", ["negative", "outside", "missing", "boolean"])
def test_invalid_exit_profiles_are_rejected(tmp_path, change):
    saved = profile()
    if change == "negative":
        saved["radius"][0] = -1
    elif change == "outside":
        saved["points"]["up"][0] = 1000
    elif change == "missing":
        saved["points"].pop("right")
    else:
        saved["points"]["down"][0] = True
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"map_exits": {"screen": saved}}))
    with pytest.raises(ValueError, match="sortie de carte"):
        Store(path)


def test_radius_uses_logical_screen_geometry_not_retina_pixels():
    from types import SimpleNamespace
    screen = SimpleNamespace(
        geometry=lambda: SimpleNamespace(width=lambda: 1920, height=lambda: 1080),
        physicalSize=lambda: SimpleNamespace(width=lambda: 480, height=lambda: 270))
    assert exit_radius(screen) == [120, 120]
