from visionguard.gestures import Detection as D, Gesture as G, GestureStabilizer


def arm(s, now=0, context="ppt"):
    s.update([], now, authorized=True, context=context)
    for i in range(3):
        s.update([D(G.NEUTRAL, .99, "Right")], now + .1 + i * .1, authorized=True, context=context)


def hold(s, gesture=G.RIGHT_L, start=1, context="ppt", authorized=True):
    results = []
    for i in range(20):
        event = s.update([D(gesture, .99, "Right")], start + i * .1,
                         authorized=authorized, context=context)
        if event:
            results.append(event)
    return results


def test_held_L_fires_once_and_rearms_only_neutral():
    s = GestureStabilizer()
    arm(s)
    assert hold(s) == [G.RIGHT_L]
    for i in range(10):
        s.update([], 4 + i * .1, authorized=True, context="ppt")
    assert hold(s, start=6) == []
    arm(s, 9)
    assert hold(s, start=10) == [G.RIGHT_L]


def test_unauthorized_never_triggers():
    s = GestureStabilizer()
    arm(s)
    assert not hold(s, authorized=False)
    assert not hold(s, start=4)  # fresh neutral required after authentication loss


def test_switching_app_cancels_held_gesture():
    s = GestureStabilizer()
    arm(s)
    assert not hold(s, context="word")
    arm(s, 4, context="word")
    assert hold(s, start=5, context="word") == [G.RIGHT_L]


def test_conflicting_hands_and_low_confidence_do_nothing():
    s = GestureStabilizer()
    arm(s)
    for i in range(30):
        assert s.update([D(G.LEFT_L, .99, "Left"), D(G.RIGHT_L, .99, "Right")],
                        1 + i * .1, authorized=True, context="ppt") is None
        assert s.update([D(G.RIGHT_L, .5, "Right")], 5 + i * .1, authorized=True, context="ppt") is None


def test_transient_fist_does_not_trigger_and_pinch_long_hold_does():
    s = GestureStabilizer()
    arm(s)
    for i in range(4):
        assert s.update([D(G.FIST, .99, "Right")], 1 + i * .05, authorized=True, context="ppt") is None
    arm(s, 2)
    assert hold(s, G.PINCH, start=3) == [G.PINCH]


def test_ambiguous_hands_cannot_rearm():
    s = GestureStabilizer()
    arm(s)
    hold(s)
    for i in range(20):
        s.update([D(G.AMBIGUOUS, .99, "Right")], 4 + i * .1, authorized=True, context="ppt")
    assert not hold(s, start=7)
