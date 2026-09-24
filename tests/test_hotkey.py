from zade.hotkey import HoldDetector

META, Z = 125, 44


def test_fires_once_after_holding_alone():
    d = HoldDetector(META, hold_s=2.0)
    d.key(META, 1, 0.0)
    assert not d.due(1.9)
    assert d.due(2.0)
    assert not d.due(3.0)  # only once per hold


def test_combo_does_not_fire():
    d = HoldDetector(META, hold_s=2.0)
    d.key(META, 1, 0.0)
    d.key(Z, 1, 0.5)  # Win+Z is a shortcut, not a hold
    assert not d.due(2.5)


def test_release_resets():
    d = HoldDetector(META, hold_s=2.0)
    d.key(META, 1, 0.0)
    d.key(META, 0, 1.0)
    assert not d.due(2.5)
    d.key(META, 1, 3.0)
    d.key(META, 2, 3.5)  # key-repeat events must not restart the timer
    assert d.due(5.0)


def test_other_key_after_trigger_cancels():
    d = HoldDetector(META, hold_s=2.0)
    d.key(META, 1, 0.0)
    assert d.due(2.0) and not d.cancelled()
    d.key(Z, 1, 3.0)  # a Win shortcut pressed after the beep
    assert d.cancelled()


def test_keyboards_found_by_id_links(tmp_path):
    from zade import hotkey

    (tmp_path / "event5").write_text("")
    (tmp_path / "event9").write_text("")
    byid = tmp_path / "by-id"
    byid.mkdir()
    (byid / "usb-ROYUAN_Keyboard-event-kbd").symlink_to(tmp_path / "event5")
    (byid / "usb-Mouse-if01-event-kbd").symlink_to(tmp_path / "event9")
    (byid / "usb-Mouse-event-mouse").symlink_to(tmp_path / "event9")
    assert hotkey.keyboard_paths(byid) == [str(tmp_path / "event5"), str(tmp_path / "event9")]
