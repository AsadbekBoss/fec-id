import math

import cv2
import numpy as np
import pytest

from recognition.align import TEMPLATE_112, align, similarity_transform
from recognition.quality import QualityLimits, blur_variance, brightness, pose_from_landmarks, post_check, pre_check

from .conftest import frontal_detection, textured_image


def test_similarity_transform_recovers_known_transform() -> None:
    ang, s, t = math.radians(17), 0.4, np.array([5.0, -3.0])
    r = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
    src = (TEMPLATE_112 - t) @ np.linalg.inv(s * r).T  # so that s*R*src + t == template
    m = similarity_transform(src, TEMPLATE_112)
    mapped = src @ m[:, :2].T + m[:, 2]
    assert np.allclose(mapped, TEMPLATE_112, atol=1e-3)


def test_similarity_transform_never_reflects() -> None:
    mirrored = TEMPLATE_112 * np.array([-1, 1], np.float32)
    m = similarity_transform(mirrored, TEMPLATE_112)
    assert np.linalg.det(m[:, :2]) > 0


def test_align_output_and_landmarks_land_on_template() -> None:
    img = np.zeros((500, 500, 3), np.uint8)
    lm = TEMPLATE_112 * 2.5 + 50
    for x, y in lm:
        cv2.circle(img, (round(float(x)), round(float(y))), 4, (255, 255, 255), -1)
    out = align(img, lm)
    assert out.shape == (112, 112, 3)
    for x, y in TEMPLATE_112:
        assert out[round(float(y)), round(float(x))].max() > 200


def test_pose_frontal_turned_and_tilted() -> None:
    yaw, roll = pose_from_landmarks(TEMPLATE_112)
    assert abs(yaw) < 3 and abs(roll) < 1
    turned = TEMPLATE_112.copy()
    turned[2, 0] += 15  # nose moved toward image right
    assert pose_from_landmarks(turned)[0] > 30
    c, s = math.cos(math.radians(25)), math.sin(math.radians(25))
    tilted = (TEMPLATE_112 - 56) @ np.array([[c, s], [-s, c]], np.float32) + 56
    assert abs(pose_from_landmarks(tilted)[1] - 25) < 1


def test_blur_and_brightness_measures() -> None:
    sharp = textured_image(112, 112)
    blurred = cv2.GaussianBlur(sharp, (0, 0), 4)
    assert blur_variance(sharp) > 40 > blur_variance(blurred)
    assert brightness(np.full((112, 112, 3), 10, np.uint8)) == pytest.approx(10)


def test_quality_gate_reasons() -> None:
    lim = QualityLimits()
    good = pre_check(frontal_detection(), lim)
    assert good.passed and good.reasons == []
    small = pre_check(frontal_detection(scale=0.5, score=0.5), lim)
    assert {"face_too_small", "eyes_too_close_in_pixels", "low_detection_score"} <= set(small.reasons)
    dark = post_check(pre_check(frontal_detection(), lim), np.full((112, 112, 3), 5, np.uint8), lim)
    assert not dark.passed and "too_dark" in dark.reasons and "blurry" in dark.reasons
    bright = post_check(pre_check(frontal_detection(), lim), np.full((112, 112, 3), 250, np.uint8), lim)
    assert "too_bright" in bright.reasons
    ok = post_check(pre_check(frontal_detection(), lim), textured_image(112, 112), lim)
    assert ok.passed
