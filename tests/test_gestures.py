import numpy as np
import pytest
from visionguard.gestures import Gesture as G, anatomical_hand, classify


def landmarks(shape):
    p = np.zeros((21, 3))
    for i, x in zip([5, 9, 13, 17], [-.4, 0, .3, .6]):
        p[i] = (x, 1, 0)
        p[i+1] = (x, 1.5, 0)
        p[i+2] = (x, 1.25, .1)
        p[i+3] = (x, 1.1, .1)
    p[1], p[2], p[3], p[4] = (-.15, .45, 0), (-.3, .6, 0), (-.2, .5, 0), (-.1, .5, 0)
    if shape in {"L", "PINCH"}:
        p[6], p[7], p[8] = (-.4, 1.6, 0), (-.4, 2, 0), (-.4, 2.4, 0)
    if shape == "L":
        p[1], p[2], p[3], p[4] = (-.3, .6, 0), (-.6, .6, 0), (-.9, .6, 0), (-1.2, .6, 0)
    if shape == "PINCH":
        p[4] = p[8] + (.05, 0, 0)
    return p


@pytest.mark.parametrize("hand,expected", [("Left", G.LEFT_L), ("Right", G.RIGHT_L)])
def test_L_geometry(hand, expected):
    assert classify(landmarks("L"), hand, .99).gesture == expected


@pytest.mark.parametrize("shape,expected", [("FIST", G.FIST), ("PINCH", G.PINCH)])
def test_exit_geometry(shape, expected):
    assert classify(landmarks(shape), "Left", .99).gesture == expected


def test_pinch_scale_invariant():
    p = landmarks("PINCH")
    for scale in [.05, 1, 10]:
        assert classify(p * scale + np.array([.4, .3, .1]), "Right", .99).gesture == G.PINCH


@pytest.mark.parametrize("confidence", [0, .5, .79])
def test_uncertain_handedness_never_triggers(confidence):
    assert classify(landmarks("L"), "Left", confidence).gesture == G.AMBIGUOUS


def test_mirrored_handedness_and_calibration():
    assert anatomical_hand("Left", mirrored_input=True) == "Left"
    assert anatomical_hand("Left", mirrored_input=False) == "Right"
    assert anatomical_hand("Left", mirrored_input=True, swap=True) == "Right"
    assert anatomical_hand("Left", mirrored_input=False, swap=True) == "Left"
    assert anatomical_hand("Unknown") == ""


@pytest.mark.parametrize("points", [np.zeros((20, 3)), np.full((21, 3), np.nan), np.zeros((21, 3))])
def test_bad_landmarks_ignored(points):
    assert classify(points, "Left", .99).gesture == G.AMBIGUOUS
