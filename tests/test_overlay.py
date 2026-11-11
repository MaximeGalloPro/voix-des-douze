"""Ground projection, eligibility and disappearance of accessibility markers."""
import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from voix_des_douze.harvest import Resource
from voix_des_douze.overlay import CalibrationOverlay, ResourceOverlay, calibration_transform, cell_point


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_calibration_projects_even_and_odd_rows_with_screen_offset():
    transform = calibration_transform((0, 13, 546),
                                      (QPointF(110, 80), QPointF(1410, 80), QPointF(160, 860)))
    # One complete even/odd stagger and the far corner, not only fitted anchors.
    assert transform.map(cell_point(14)) == QPointF(160, 100)
    assert transform.map(cell_point(28)) == QPointF(110, 120)
    assert transform.map(cell_point(559)) == QPointF(1460, 860)


def test_unusable_calibration_is_rejected():
    with pytest.raises(ValueError):
        calibration_transform((0, 1, 2), (QPointF(0, 0), QPointF(10, 0), QPointF(0, 10)))
    with pytest.raises(ValueError):
        calibration_transform((0, 13, 546), (QPointF(10, 10), QPointF(20, 20), QPointF(30, 30)))
    for cell in (-1, 560):
        with pytest.raises(ValueError):
            cell_point(cell)


def test_only_personally_harvestable_cells_appear_and_stale_markers_clear(app):
    overlay = ResourceOverlay()
    overlay.foreground = lambda: 123
    overlay.pid = 123
    overlay.enabled = True
    try:
        overlay.calibrate((0, 13, 546), (QPointF(100, 80), QPointF(750, 80), QPointF(125, 470)),
                          QRect(0, 0, 900, 600))
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


def test_calibration_collects_three_clicks_or_cancels_without_result(app):
    results = []
    calibration = CalibrationOverlay((0, 13, 546), app.primaryScreen())
    calibration.finished.connect(results.append)
    calibration.show()
    QTest.mouseClick(calibration, Qt.LeftButton, pos=QPoint(100, 150))
    QTest.mouseClick(calibration, Qt.LeftButton, pos=QPoint(500, 150))
    assert results == []
    QTest.mouseClick(calibration, Qt.LeftButton, pos=QPoint(120, 450))
    assert results == [(QPointF(100, 150), QPointF(500, 150), QPointF(120, 450))]
    assert not calibration.isVisible()
    cancelled = CalibrationOverlay((0, 13, 546), app.primaryScreen())
    cancelled.finished.connect(results.append)
    cancelled.show()
    QTest.keyClick(cancelled, Qt.Key_Escape)
    assert results[-1] is None
    assert not cancelled.isVisible()
