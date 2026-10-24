import numpy as np
from PyQt6.QtGui import QColor, QImage, QPainter

from core.avatar import HoloAvatar


def test_avatar_pose_remains_finite_across_activity_states():
    avatar = HoloAvatar()
    states = ("IDLE", "LISTENING", "THINKING", "SPEAKING", "SLEEPING")
    for index in range(300):
        state = states[index % len(states)]
        avatar.step(
            1 / 30,
            (index % 11) / 10,
            speaking=state == "SPEAKING",
            state=state,
            v_open=0.7 if state == "SPEAKING" else 0.0,
            v_wide=0.4,
            v_level=1200 if state == "SPEAKING" else 0,
        )
        vertices, normals = avatar._pose()
        assert np.isfinite(vertices).all()
        assert np.isfinite(normals).all()


def test_avatar_paints_smooth_face_to_cpu_fallback_image():
    image = QImage(360, 420, QImage.Format.Format_ARGB32_Premultiplied)
    background = QColor(7, 12, 20)
    image.fill(background)
    avatar = HoloAvatar()
    for _ in range(12):
        avatar.step(1 / 30, 0.35, speaking=True, state="SPEAKING")

    painter = QPainter(image)
    avatar.paint(painter, 180, 210, 120, QColor("#38d8ff"), QColor("#bb6cff"), background)
    painter.end()

    different = sum(
        image.pixelColor(x, y) != background
        for y in range(image.height())
        for x in range(image.width())
    )
    assert different > 10_000
