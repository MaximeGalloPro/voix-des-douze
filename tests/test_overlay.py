"""Ground projection, eligibility and disappearance of accessibility markers."""
import pytest
from PySide6.QtCore import QEventLoop, QPointF, QRect, Qt, QTimer
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


def test_debug_is_opt_in_scoped_and_ignores_text(app, tmp_path):
    normal = AdjustmentOverlay(app.primaryScreen(), (), (0, 0, 1), debug_directory=tmp_path)
    normal.show()
    QTest.keyClick(normal, Qt.Key_Right)
    normal.close()
    assert list(tmp_path.iterdir()) == []

    import json
    panel = AdjustmentOverlay(app.primaryScreen(), (), (0, 0, 1),
                              debug=True, debug_directory=tmp_path)
    panel.show()
    QTest.keyClicks(panel, "secret")
    QTest.keyClick(panel, Qt.Key_Right, Qt.ShiftModifier)
    QTest.keyClick(panel, Qt.Key_Escape)
    journal = panel.debug.path.read_text()
    rows = [json.loads(line) for line in journal.splitlines()]
    assert "secret" not in journal
    assert [row["touche"] for row in rows if row["étape"] == "qt"] == ["droite", "échap"]
    assert [row["réglage"] for row in rows if row["étape"] == "appliqué"] == [[10, 0, 1]]
    assert rows[-1]["étape"] == "fin"
    assert not panel.debug.timer.isActive()
    assert panel.debug.path.stat().st_mode & 0o777 == 0o600
    QTest.keyClick(panel, Qt.Key_Left)
    assert panel.debug.path.read_text() == journal


def test_debug_write_limit_stops_logging_but_not_adjustment(app, tmp_path):
    panel = AdjustmentOverlay(app.primaryScreen(), (), (0, 0, 1),
                              debug=True, debug_directory=tmp_path)
    panel.show()
    panel.debug.MAX_BYTES = 1
    QTest.keyClick(panel, Qt.Key_Right)
    assert panel.adjustment == [1, 0, 1]
    assert panel.debug.file is None
    assert panel.debug.error
    panel.close()


def test_click_cross_uses_screen_origin_and_expires_without_stealing_input(app):
    overlay = ResourceOverlay()
    overlay.pid = 123
    overlay.foreground = lambda: 123
    overlay.click_timer.setInterval(20)
    try:
        overlay.show_click_target(QPointF(2020.5, 310.25), QRect(1920, 100, 900, 600))
        assert overlay.click_target == QPointF(100.5, 210.25)
        assert overlay.isVisible()
        assert overlay.windowFlags() & Qt.WindowTransparentForInput
        assert overlay.windowFlags() & Qt.WindowDoesNotAcceptFocus
        overlay.foreground = lambda: 456
        overlay.refresh_visibility()
        assert not overlay.isVisible()
        loop = QEventLoop()
        overlay.click_timer.timeout.connect(loop.quit)
        QTimer.singleShot(1000, loop.quit)
        loop.exec()
        overlay.foreground = lambda: 123
        overlay.refresh_visibility()
        assert overlay.click_target is None
        assert not overlay.isVisible()
    finally:
        overlay.timer.stop()
        overlay.click_timer.stop()
        overlay.close()


def test_protection_debug_remains_visible_without_resources_but_hides_outside_game(app):
    from voix_des_douze.monster_safety import MonsterSafety
    overlay = ResourceOverlay()
    overlay.pid = 123
    overlay.foreground = lambda: 123
    overlay.protection_safety = MonsterSafety()
    try:
        overlay.configure(QRect(0, 0, 900, 600), (0, 0, 1))
        overlay.protection_enabled = True
        overlay.refresh_visibility()
        assert overlay.isVisible()
        overlay.foreground = lambda: 456
        overlay.refresh_visibility()
        assert not overlay.isVisible()
        overlay.foreground = lambda: 123
        overlay.calibrating = True
        overlay.refresh_visibility()
        assert not overlay.isVisible()
        overlay.calibrating = False
        overlay.refresh_visibility()
        assert overlay.isVisible()
        overlay.protection_enabled = False
        overlay.refresh_visibility()
        assert not overlay.isVisible()
    finally:
        overlay.timer.stop()
        overlay.close()
