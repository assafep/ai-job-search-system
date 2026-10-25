"""Answers to the setup questions, read from deployment/defaults.json."""
import json
from pathlib import Path

from .state import ProvisioningFailure

DEFAULTS_PATH = Path(__file__).resolve().parents[1] / 'deployment' / 'defaults.json'
KEYS = ('setup', 'action', 'drive_mode', 'drive_path')


def load_answers(path=None):
    path = Path(path or DEFAULTS_PATH)
    if not path.is_file():
        raise ProvisioningFailure('Missing setup answers: ' + str(path))
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ProvisioningFailure(str(path) + ' must be a JSON object')
    answers = {}
    for key in KEYS:
        value = data.get(key)
        if value is None or value == '':
            continue
        if not isinstance(value, str):
            raise ProvisioningFailure(str(path) + ' key ' + key + ' must be a string')
        answers[key] = value
    return answers


def choose(flag, answers, key):
    """A command-line value wins. Otherwise use the answers file."""
    if flag:
        return flag
    return answers.get(key)
