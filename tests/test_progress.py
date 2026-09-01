from __future__ import annotations

import next_pass.progress as progress_mod


def test_progress_enabled_false_when_stdout_not_a_tty(monkeypatch):
    class NonTtyStream:
        def isatty(self):
            return False

    monkeypatch.setattr(progress_mod.sys, "__stdout__", NonTtyStream())
    assert progress_mod._progress_enabled() is False


def test_progress_enabled_false_when_stdout_is_none(monkeypatch):
    monkeypatch.setattr(progress_mod.sys, "__stdout__", None)
    assert progress_mod._progress_enabled() is False


def test_overpass_progress_disabled_yields_noop_controller(monkeypatch):
    monkeypatch.setattr(progress_mod, "_progress_enabled", lambda: False)

    with progress_mod.overpass_progress(3) as controller:
        assert controller._progress is None
        with controller.satellite("Sentinel-1") as step_cb:
            # Must not raise and must not touch a live Progress instance.
            step_cb("Loading collection")


def test_overpass_progress_disabled_for_zero_satellites():
    with progress_mod.overpass_progress(0) as controller:
        assert controller._progress is None


def test_progress_controller_init_accepts_none_master_task():
    controller = progress_mod.ProgressController(None, None)
    assert controller._progress is None
    assert controller._master is None
