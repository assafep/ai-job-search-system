import json

from provisioning.gmail_login import GMAIL_READONLY, gmail_token_saved, load_gmail_token, save_gmail_token


def test_saved_gmail_token_is_read_only(tmp_path):
    path = tmp_path / 'gmail-user.json'
    save_gmail_token({
        'GOOGLE_CLIENT_ID': 'id',
        'GOOGLE_CLIENT_SECRET': 'secret',
        'GOOGLE_REFRESH_TOKEN': 'refresh',
    }, path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert gmail_token_saved(path)
    assert load_gmail_token(path)['refresh_token'] == 'refresh'
    saved = json.loads(path.read_text())
    assert GMAIL_READONLY in saved['scope'].split()
    saved['scope'] = 'https://www.googleapis.com/auth/gmail.modify'
    path.write_text(json.dumps(saved))
    assert not gmail_token_saved(path)
    assert load_gmail_token(path) is None
