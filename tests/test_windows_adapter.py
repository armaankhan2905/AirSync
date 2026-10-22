from unittest.mock import Mock
import pytest
import visionguard.security as security


@pytest.mark.parametrize("desktop,expected", [("Default", False), ("Winlogon", True)])
def test_session_desktop_query_is_read_only(monkeypatch, desktop, expected):
    user = Mock()
    user.OpenInputDesktop.return_value = 123
    def information(handle, kind, buffer, size, needed):
        buffer.value = desktop
        return 1
    user.GetUserObjectInformationW.side_effect = information
    monkeypatch.setattr(security.ctypes, "WinDLL", Mock(return_value=user))
    assert security.session_locked() is expected
    user.CloseDesktop.assert_called_once_with(123)
    user.SwitchDesktop.assert_not_called()
    user.LockWorkStation.assert_not_called()


def test_session_query_access_denied_fails_closed(monkeypatch):
    user = Mock()
    user.OpenInputDesktop.return_value = 0
    monkeypatch.setattr(security.ctypes, "WinDLL", Mock(return_value=user))
    assert security.session_locked()
    user.SwitchDesktop.assert_not_called()
    user.CloseDesktop.assert_not_called()
