from concurrent.futures import ThreadPoolExecutor
import time

from provisioning.browser import open_browser


def test_container_browser_request_receives_host_acknowledgement(tmp_path, monkeypatch):
    monkeypatch.setenv('AJSS_BROWSER_RELAY_DIR', str(tmp_path))
    url = 'https://accounts.google.com/o/oauth2/v2/auth?state=test&scope=test'
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(open_browser, url, 2)
        deadline = time.monotonic() + 2
        while not (tmp_path / 'request.url').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (tmp_path / 'request.url').read_text().strip() == url
        (tmp_path / 'result').write_text('1')
        assert result.result(timeout=2)


def test_unavailable_host_browser_returns_manual_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv('AJSS_BROWSER_RELAY_DIR', str(tmp_path))
    assert not open_browser('https://accounts.google.com/o/oauth2/v2/auth?state=test', timeout=.01)


def test_direct_browser_failure_returns_manual_fallback(monkeypatch):
    from provisioning import browser
    monkeypatch.delenv('AJSS_BROWSER_RELAY_DIR', raising=False)
    monkeypatch.setattr(browser.webbrowser, 'open', lambda url: False)
    assert not open_browser('https://accounts.google.com/o/oauth2/v2/auth?state=test')
