"""Request a host browser from containerized provisioning, with a manual fallback."""
import os
from pathlib import Path
import tempfile
import time
import webbrowser


def open_browser(url, timeout=5):
    directory = os.environ.get('AJSS_BROWSER_RELAY_DIR')
    if not directory:
        try:
            return bool(webbrowser.open(url))
        except (OSError, webbrowser.Error):
            return False
    directory = Path(directory)
    request = directory / 'request.url'
    result = directory / 'result'
    try:
        result.unlink(missing_ok=True)
        fd, temporary = tempfile.mkstemp(dir=directory, prefix='.browser-')
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(url + '\n')
            os.replace(temporary, request)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if result.is_file():
                return result.read_text().strip() == '1'
            time.sleep(0.05)
    except OSError:
        return False
    return False
