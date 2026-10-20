from eegagent.core.segment import WindowPolicy, plan_windows


def test_emotion_windows_cover_expected_interval():
    windows = plan_windows(25.0, WindowPolicy(10.0, 5.0, partial="drop"))
    assert [(w.start_s, w.end_s) for w in windows] == [
        (0.0, 10.0),
        (5.0, 15.0),
        (10.0, 20.0),
        (15.0, 25.0),
    ]


def test_long_signal_is_uniformly_budgeted():
    windows = plan_windows(100.0, WindowPolicy(10.0, 5.0, max_windows=4))
    assert len(windows) == 4
    assert windows[0].start_s == 0.0
    assert windows[-1].end_s == 100.0


def test_short_signal_can_be_padded():
    windows = plan_windows(3.0, WindowPolicy(4.0, 2.0, partial="pad"))
    assert len(windows) == 1
    assert windows[0].start_s == 0.0
    assert windows[0].end_s == 3.0
    assert windows[0].pad_right_s == 1.0
