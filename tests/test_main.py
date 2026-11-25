import signal

import pytest

import scripts.main as main_module


def test_sigterm_exits_cleanly_and_closes_updater(monkeypatch):
    class FakeUpdater:
        closed = False

        def republish(self):
            return False

        def close(self):
            self.closed = True

    updater = FakeUpdater()
    monkeypatch.setenv("PYTHON_IN_DOCKER", "1")
    monkeypatch.setenv("ACCOUNT", "account@example.com")
    monkeypatch.setenv("PASSWORD", "password1")
    monkeypatch.setenv("FETCH_ON_STARTUP", "false")
    monkeypatch.setattr(main_module.ErrorWatcher, "init", lambda **_kwargs: None)
    monkeypatch.setattr(main_module, "SensorUpdater", lambda: updater)
    monkeypatch.setattr(main_module, "DataFetcher", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(main_module, "schedule_jobs", lambda *_args: None)

    def receive_sigterm():
        signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr(main_module, "run_forever", receive_sigterm)
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)
    try:
        with pytest.raises(SystemExit) as exit_info:
            main_module.main()
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)

    assert exit_info.value.code == 0
    assert updater.closed is True
