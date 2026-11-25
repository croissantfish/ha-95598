from scripts.support.login_manager import LoginManager


def test_rk001_qr_fallback_opens_login_page_first(monkeypatch):
    manager = object.__new__(LoginManager)
    calls = []
    manager._is_password_login_in_cooldown = lambda: True
    manager._open_login_page = lambda driver: calls.append(("open", driver))
    manager._fallback_login = lambda driver: calls.append(("fallback", driver)) or True
    monkeypatch.setattr(LoginManager, "_allow_qr_after_rk001", lambda _self=None: True)
    driver = object()

    assert manager._login_with_credential_rotation(driver) is True
    assert calls == [("open", driver), ("fallback", driver)]
