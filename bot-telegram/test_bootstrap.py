import json

from bootstrap import ensure_panel_account
from auth import password_digest


def _matches(path, username, password):
    account = json.loads(path.read_text(encoding='utf-8'))
    return (
        account['username'] == username
        and password_digest(password, bytes.fromhex(account['salt'])) == account['digest']
    )


def test_first_boot_uses_configured_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('COMMERCIAL_TEST_MODE', '1')
    monkeypatch.setenv('PANEL_BOOTSTRAP_USERNAME', 'testadmin')
    monkeypatch.setenv('PANEL_BOOTSTRAP_PASSWORD', 'first-password-2026')
    assert ensure_panel_account(tmp_path) is True
    assert _matches(tmp_path / 'panel-account.json', 'testadmin', 'first-password-2026')


def test_test_mode_resyncs_changed_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('COMMERCIAL_TEST_MODE', '1')
    monkeypatch.setenv('PANEL_BOOTSTRAP_USERNAME', 'testadmin')
    monkeypatch.setenv('PANEL_BOOTSTRAP_PASSWORD', 'first-password-2026')
    ensure_panel_account(tmp_path)

    monkeypatch.setenv('PANEL_BOOTSTRAP_PASSWORD', 'second-password-2026')
    assert ensure_panel_account(tmp_path) is True
    assert _matches(tmp_path / 'panel-account.json', 'testadmin', 'second-password-2026')


def test_normal_mode_does_not_overwrite_existing_account(tmp_path, monkeypatch):
    monkeypatch.setenv('COMMERCIAL_TEST_MODE', '1')
    monkeypatch.setenv('PANEL_BOOTSTRAP_USERNAME', 'testadmin')
    monkeypatch.setenv('PANEL_BOOTSTRAP_PASSWORD', 'first-password-2026')
    ensure_panel_account(tmp_path)

    before = (tmp_path / 'panel-account.json').read_text(encoding='utf-8')
    monkeypatch.setenv('COMMERCIAL_TEST_MODE', '0')
    monkeypatch.setenv('PANEL_BOOTSTRAP_PASSWORD', 'second-password-2026')
    assert ensure_panel_account(tmp_path) is False
    assert (tmp_path / 'panel-account.json').read_text(encoding='utf-8') == before
