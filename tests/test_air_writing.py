from dataclasses import replace
import numpy as np
import pytest
from visionguard.air_writing import AirWriting, CoordinateMapper, SlideContext, TrajectoryFilter, fit_slide
from visionguard.config import Settings
from visionguard.gestures import Gesture as G, Detection, classify


class Driver:
    def __init__(self):
        self.air = AirWriting(Settings())
        self.now = 100.
        self.slide = SlideContext("show-a", 1, 100, (0, 0, 1600, 900), self.now)
        self.epoch = 1
        self.authorized = True

    def frame(self, gesture=None, point=None, confidence=.99, hand="Right", dt=.05):
        self.now += dt
        self.slide = replace(self.slide, observed_at=self.now)
        detections = [] if gesture is None else [Detection(gesture, confidence, hand)]
        return self.air.update(detections, point, self.now, context=self.slide,
                               authorized=self.authorized, epoch=self.epoch)

    def hold(self, gesture, count=15, **kwargs):
        return [result for _ in range(count) if (result := self.frame(gesture, **kwargs))]

    def neutral(self):
        self.hold(G.TWO_FINGERS, 3)

    def writing(self):
        self.neutral()
        self.hold(G.THREE_FINGERS)
        self.neutral()

    def draw(self):
        for i in range(10):
            self.frame(G.INDEX, (.3 + .02*i, .4 + .01*i))


def test_three_fingers_toggle_on_off_with_neutral_and_no_repeat():
    d = Driver()
    d.neutral()
    d.hold(G.THREE_FINGERS, 60)
    assert d.air.mode
    assert not d.air.armed
    d.neutral()
    d.hold(G.THREE_FINGERS, 60)
    assert not d.air.mode


def test_short_or_low_confidence_toggle_does_not_activate():
    d = Driver()
    d.neutral()
    d.hold(G.THREE_FINGERS, 5)
    d.frame(None)
    d.hold(G.THREE_FINGERS, 30, confidence=.7)
    assert not d.air.mode


def test_continuous_index_stroke_and_finger_lowering():
    d = Driver()
    d.writing()
    d.draw()
    strokes = d.air.snapshot()["strokes"]
    assert len(strokes) == 1 and len(strokes[0][2]) == 9
    assert strokes[0][0] == "#ff3030"
    assert all(a[0] < b[0] for a, b in zip(strokes[0][2], strokes[0][2][1:]))
    d.frame(G.NEUTRAL)
    assert d.air.stroke is None
    d.neutral()
    d.draw()
    assert len(d.air.snapshot()["strokes"]) == 2


@pytest.mark.parametrize("end", [None, G.AMBIGUOUS, G.LEFT_L, G.PINCH, G.THREE_FINGERS])
def test_hand_disappearance_or_other_gesture_lifts_pen(end):
    d = Driver()
    d.writing()
    d.draw()
    d.frame(end)
    assert d.air.stroke is None


def test_index_cannot_draw_without_writing_mode_or_neutral_after_toggle():
    d = Driver()
    d.neutral()
    d.draw()
    assert not d.air.ink
    d.hold(G.THREE_FINGERS)
    d.draw()
    assert not d.air.ink
    d.neutral()
    d.draw()
    assert d.air.ink


def test_five_fingers_clear_only_current_slide_once_and_keep_mode():
    d = Driver()
    d.writing()
    d.draw()
    d.slide = replace(d.slide, slide=2)
    d.neutral()
    d.draw()
    d.neutral()
    d.hold(G.OPEN_PALM)
    assert d.air.mode
    assert ("show-a", 1) in d.air.ink and ("show-a", 2) not in d.air.ink
    revision = d.air.revision
    d.hold(G.OPEN_PALM, 50)
    assert d.air.revision == revision


def test_clear_requires_stable_palm_and_neutral_after_command():
    d = Driver()
    d.writing()
    d.draw()
    d.neutral()
    d.hold(G.OPEN_PALM, 6)
    d.frame(G.AMBIGUOUS)
    d.hold(G.OPEN_PALM, 6)
    assert d.air.ink
    d.hold(G.OPEN_PALM, 4, confidence=.1)
    assert d.air.ink


def test_navigation_suppressed_but_exit_still_works_in_writing_mode():
    d = Driver()
    d.writing()
    assert d.hold(G.LEFT_L) == []
    assert d.hold(G.RIGHT_L) == []
    assert d.hold(G.FIST) == [G.FIST]
    assert not d.air.mode


@pytest.mark.parametrize("gesture", [G.LEFT_L, G.RIGHT_L, G.FIST, G.PINCH])
def test_existing_navigation_and_exit_available_when_mode_off(gesture):
    d = Driver()
    d.neutral()
    assert d.hold(gesture) == [gesture]


def test_unauthorized_cannot_toggle_draw_or_clear_and_existing_ink_preserved():
    d = Driver()
    d.writing()
    d.draw()
    before = d.air.snapshot()["strokes"]
    d.authorized = False
    d.draw()
    d.hold(G.OPEN_PALM)
    d.hold(G.THREE_FINGERS)
    assert not d.air.mode and d.air.stroke is None
    assert d.air.snapshot()["strokes"] == before


def test_slide_identity_change_never_transfers_or_connects_strokes():
    d = Driver()
    d.writing()
    d.draw()
    first = d.air.snapshot()["strokes"]
    d.slide = replace(d.slide, slide=2)
    d.draw()  # Continuing a raised index cannot spill ink onto the next slide.
    assert not d.air.snapshot()["strokes"]
    d.neutral()
    d.draw()
    assert d.air.snapshot()["strokes"]
    d.slide = replace(d.slide, slide=1)
    d.frame(G.TWO_FINGERS)
    assert d.air.snapshot()["strokes"] == first


def test_different_show_or_identity_requires_new_toggle():
    d = Driver()
    d.writing()
    d.draw()
    d.epoch += 1
    d.frame(G.INDEX, (.5, .5))
    assert not d.air.mode
    d.writing()
    d.slide = replace(d.slide, session="another-show")
    d.frame(G.INDEX, (.5, .5))
    assert not d.air.mode and not d.air.snapshot()["strokes"]


def test_closed_slideshow_discards_ink_transient_com_error_does_not():
    d = Driver()
    d.writing()
    d.draw()
    d.air.reconcile([], d.now, known=False)
    assert d.air.ink
    d.air.reconcile([], d.now, known=True)
    assert not d.air.ink and not d.air.mode and d.air.context is None


def test_camera_gap_and_stale_slide_metadata_stop_input():
    d = Driver()
    d.writing()
    d.draw()
    d.air.reconcile([d.slide], d.now + .3)
    assert d.air.stroke is None
    before = d.air.snapshot()["strokes"]
    d.air.update([Detection(G.INDEX, .99, "Right")], (.6, .6), d.now + 1,
                 context=d.slide, authorized=True, epoch=1)
    assert d.air.snapshot()["strokes"] == before
    assert not d.air.mode


def test_two_hands_do_not_draw_or_clear():
    d = Driver()
    d.writing()
    d.draw()
    before = d.air.snapshot()["strokes"]
    for _ in range(20):
        d.now += .05
        d.air.update([Detection(G.INDEX, .99, "Right"), Detection(G.OPEN_PALM, .99, "Left")],
                     (.5, .5), d.now, context=replace(d.slide, observed_at=d.now), authorized=True, epoch=1)
    assert d.air.snapshot()["strokes"] == before


def test_coordinate_calibration_mirroring_and_outside_pen_lift():
    corners = [[.2, .25], [.8, .2], [.75, .85], [.25, .8]]
    mapper = CoordinateMapper(corners)
    for point, result in zip(corners, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        assert mapper.map(point) == pytest.approx(result)
        assert mapper.map((1-point[0], point[1]), mirrored=False) == pytest.approx(result)
    assert mapper.map((0, 0)) is None
    assert mapper.map((float("nan"), .5)) is None


@pytest.mark.parametrize("corners", [[], [[.5, .5]]*4, [[0,0], [1,1], [0,1], [1,0]],
    [[.4,.4], [.45,.4], [.45,.45], [.4,.45]], [[-1,0], [1,0], [1,1], [0,1]]])
def test_invalid_calibration_rejected(corners):
    with pytest.raises(ValueError):
        CoordinateMapper(corners)


def test_aspect_fit_resize_and_negative_monitor_origin():
    assert fit_slide((-1920, 0, 1920, 1200), 16/9) == (-1920, 60, 1920, 1080)
    assert fit_slide((20, 30, 800, 600), 16/9) == (20, 105, 800, 450)
    # A normalized center remains at the new content center after resizing.
    for rect in [(-1920, 0, 1920, 1200), (20, 30, 800, 600)]:
        x, y, w, h = fit_slide(rect, 16/9)
        assert (x+w*.5, y+h*.5) == pytest.approx((rect[0]+rect[2]/2, rect[1]+rect[3]/2))


def test_smoothing_reduces_jitter_and_resets_after_gap():
    smooth = TrajectoryFilter(.8)
    values = [smooth.update((.5 + (-1)**i*.02, .5), i*.05)[0] for i in range(30)]
    assert np.std(values[10:]) < .02
    assert smooth.update((.8, .3), 10) == (.8, .3)
    smooth.reset()
    assert smooth.update((.2, .4), 11) == (.2, .4)


def test_bounded_ink_storage_stops_instead_of_growing_forever():
    d = Driver()
    d.air.MAX_POINTS = 5
    d.writing()
    d.draw()
    assert sum(len(s.points) for ss in d.air.ink.values() for s in ss) == 5


def hand_points(extended, thumb=False):
    p = np.zeros((21, 3))
    for n, (mcp, x) in enumerate(zip([5, 9, 13, 17], [-.4, 0, .3, .6])):
        p[mcp] = (x, 1, 0)
        p[mcp+1] = (x, 1.5, 0)
        p[mcp+2] = (x, 2, 0) if n in extended else (x, 1.25, .1)
        p[mcp+3] = (x, 2.4, 0) if n in extended else (x, 1.1, .1)
    p[1:5] = [(-.15,.45,0), (-.3,.6,0), (-.2,.5,0), (-.1,.5,0)]
    if thumb:
        p[1:5] = [(-.3,.6,0), (-.6,.6,0), (-.9,.6,0), (-1.2,.6,0)]
    return p


@pytest.mark.parametrize("fingers,thumb,expected", [({0}, False, G.INDEX), ({0,1,2}, False, G.THREE_FINGERS),
    ({0,1}, False, G.TWO_FINGERS), ({0,1,2,3}, True, G.OPEN_PALM), ({0}, True, G.RIGHT_L)])
def test_exact_finger_geometry_and_mirror_invariance(fingers, thumb, expected):
    p = hand_points(fingers, thumb)
    assert classify(p, "Right", .99).gesture == expected
    p[:,0] *= -1
    assert classify(p, "Right", .99).gesture == expected


@pytest.mark.parametrize("key,value", [("writing_width", 0), ("writing_hold", .1),
    ("writing_color", "not-a-color"), ("writing_confidence", float("nan")), ("writing_calibration", [])])
def test_bad_settings_rejected(key, value):
    with pytest.raises(ValueError):
        replace(Settings(), **{key:value}).validate()
