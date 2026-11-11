"""Ground projection, eligibility and disappearance of accessibility markers."""
import pytest
from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from voix_des_douze.harvest import Resource
from voix_des_douze.overlay import AdjustmentOverlay, ResourceOverlay, fullscreen_transform, cell_point
from voix_des_douze.settings import Store


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_fullscreen_shift_and_scale_keep_the_centre_and_grid_spacing():
    geometry = QRect(1920, 0, 1920, 1080)
    original = fullscreen_transform(geometry, (0, 0, 1))
    shifted = fullscreen_transform(geometry, (30, -20, 1))
    enlarged = fullscreen_transform(geometry, (30, -20, 2))
    centre = QPointF(6.75, 19.5)
    assert shifted.map(centre) == QPointF(990, 520)
    assert enlarged.map(centre) == shifted.map(centre)
    for cell in (0, 14, 28, 559):
        assert shifted.map(cell_point(cell)) == original.map(cell_point(cell)) + QPointF(30, -20)
        assert enlarged.map(cell_point(cell)) - shifted.map(centre) == (
            shifted.map(cell_point(cell)) - shifted.map(centre)) * 2
    for cell in (-1, 560):
        with pytest.raises(ValueError):
            cell_point(cell)


def test_only_personally_harvestable_cells_appear_and_stale_markers_clear(app):
    overlay = ResourceOverlay()
    overlay.foreground = lambda: 123
    overlay.pid = 123
    overlay.enabled = True
    try:
        overlay.configure(QRect(0, 0, 900, 600), (0, 0, 1))
        overlay.set_resources((Resource(1, 33, "Châtaignier", 100, 0, True),
                               Resource(2, 33, "Interdite", 101, 0, False),
                               Resource(3, 33, "Immature", 102, 1, True),
                               Resource(4, 33, "Inconnue", 103, 9, True),
                               Resource(5, 33, "Case inconnue", 560, 0, True)))
        assert [marker.label for marker in overlay.markers] == ["Châtaignier"]
        assert overlay.isVisible()
        overlay.foreground = lambda: 456
        overlay.refresh_visibility()
        assert not overlay.isVisible()
        overlay.foreground = lambda: 123
        overlay.refresh_visibility()
        assert overlay.isVisible()
        overlay.calibrating = True
        overlay.refresh_visibility()
        assert not overlay.isVisible()
        overlay.calibrating = False
        overlay.set_resources((Resource(1, 33, "Châtaignier", 100, 1, True),))
        assert overlay.markers == ()
        assert not overlay.isVisible()
        overlay.set_resources((Resource(1, 33, "Châtaignier", 100, 0, True),))
        assert overlay.isVisible()
        overlay.set_resources(())
        assert overlay.markers == ()
        assert not overlay.isVisible()
    finally:
        overlay.close()
        overlay.timer.stop()


def test_keyboard_adjustment_commits_and_persists_without_mutating_on_cancel(app, tmp_path):
    settings = Store(tmp_path / "settings.json")
    key = "screen:1920x1080"
    saved = [12, 24, 1]
    settings.settings.overlay_adjustments[key] = saved
    results = []
    adjustment = AdjustmentOverlay(app.primaryScreen(), (), saved)
    adjustment.finished.connect(results.append)
    adjustment.show()
    QTest.keyClick(adjustment, Qt.Key_Right)
    QTest.keyClick(adjustment, Qt.Key_Down, Qt.ShiftModifier)
    QTest.keyClick(adjustment, Qt.Key_Plus)
    assert saved == [12, 24, 1]
    assert results == []
    QTest.keyClick(adjustment, Qt.Key_Return)
    assert results == [(13, 34, 1.01)]
    assert not adjustment.isVisible()
    settings.settings.overlay_adjustments[key] = list(results[0])
    settings.save()
    restored = Store(settings.path).settings.overlay_adjustments[key]
    assert restored == [13, 34, 1.01]
    cancelled = AdjustmentOverlay(app.primaryScreen(), (), restored)
    cancelled.finished.connect(results.append)
    cancelled.show()
    QTest.keyClick(cancelled, Qt.Key_Left)
    QTest.keyClick(cancelled, Qt.Key_Minus)
    QTest.keyClick(cancelled, Qt.Key_Escape)
    assert results[-1] is None
    assert Store(settings.path).settings.overlay_adjustments[key] == [13, 34, 1.01]
    assert not cancelled.isVisible()


def test_keyboard_scaling_is_bounded(app):
    adjustment = AdjustmentOverlay(app.primaryScreen(), (), (0, 0, 3))
    try:
        QTest.keyClick(adjustment, Qt.Key_Plus)
        assert adjustment.adjustment[2] == 3
        adjustment.adjustment[2] = 0.2
        QTest.keyClick(adjustment, Qt.Key_Minus)
        assert adjustment.adjustment[2] == 0.2
    finally:
        adjustment.close()


def test_adjustment_includes_unavailable_resources_but_excludes_unknown_cells(app):
    resources = (
        Resource(1, 33, "Mature", 100, 0, True),
        Resource(2, 33, "Épuisée", 200, 1, False),
        Resource(3, 33, "En récolte", 300, 2, False),
        Resource(4, 33, "Action indisponible", 400, 0, False),
        Resource(5, 33, "État inconnu", 500, 9, False),
        Resource(6, 33, "Sans position", -1, 0, True),
        Resource(7, 33, "Hors carte", 560, 0, True),
    )
    adjustment = AdjustmentOverlay(app.primaryScreen(), resources, (0, 0, 1))
    try:
        assert adjustment.resources == resources[:5]
    finally:
        adjustment.close()
