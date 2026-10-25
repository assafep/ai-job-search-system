"""Local Chrome submission adapter using visible Comeet form controls."""

import json
import os
import shutil
import subprocess
import tempfile
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SUBMISSION_MODEL = "grok-4.7"


ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                },
            },
        }
    },
}


def form_answers(context, provider=None):
    """Ask grok-4.7 for extra form answers. Identity fields stay on the profile."""
    from adapters.llm.xai_provider import XAIProvider
    effort = context.get("reasoning_effort") or "medium"
    model = provider or XAIProvider(model=SUBMISSION_MODEL).with_reasoning_effort(effort)
    application = context["application"]
    profile = context.get("applicant_profile") or {}
    result = model.generate_with_schema(
        "Answer extra job-application questions using only the supplied profile and posting. "
        "Do not invent employers, dates, titles, or skills. Use an empty value when the profile "
        "does not contain the answer. Include a personal note only when the profile supports one.",
        {
            "company": application.get("company"),
            "role": application.get("role"),
            "posting_url": application.get("posting_url"),
            "profile": profile,
        },
        ANSWER_SCHEMA,
    )
    answers = result.get("answers") if isinstance(result, dict) else None
    if not isinstance(answers, list):
        raise ValueError("submission model did not return answers")
    return answers


def _attempt(context, logic, obstacles, tackled=None):
    return {
        "model": SUBMISSION_MODEL,
        "effort": context.get("reasoning_effort") or "medium",
        "logic": logic,
        "obstacles": obstacles,
        "tackled": tackled or [],
    }


def _blocked(context, logic, blockers, failure_kind=None):
    result = {
        "status": "Blocked",
        "blockers": blockers,
        "attempt": _attempt(context, logic, blockers),
    }
    if failure_kind:
        result["failure_kind"] = failure_kind
    return result


def comeet_destination(application):
    """The Comeet form is the posting URL already stored on the application."""
    parsed = urllib.parse.urlsplit(application.get('posting_url') or '')
    parts = [part for part in parsed.path.split('/') if part]
    if (parsed.hostname or '').lower() == 'www.comeet.com' and len(parts) >= 3 and parts[0] == 'jobs':
        board = urllib.parse.urlunsplit((parsed.scheme or 'https', parsed.hostname, '/' + '/'.join(parts[:3]), '', ''))
        return {
            'application_url': parsed.geturl(),
            'verification_url': board,
            'company': application.get('company'),
            'role': application.get('role'),
        }
    return None


def submit(context):
    application = context['application']
    destination = comeet_destination(application)
    logic = ["Opened the Comeet posting URL stored on the application."]
    if not destination:
        return _blocked(context, logic, ['The posting URL is not a Comeet application form.'])
    node = shutil.which('node')
    chrome = os.environ.get('BROWSER_EXECUTABLE') or shutil.which('google-chrome') or shutil.which('chromium')
    if not node or not chrome:
        return _blocked(context, logic, ['Local Node.js and Chrome are required by the browser submission adapter.'], 'system_error')
    with tempfile.TemporaryDirectory(prefix='job-application-') as directory:
        name = os.path.basename(context['package']['name'])
        package_path = os.path.join(directory, name)
        with open(package_path, 'wb') as handle:
            handle.write(context['package_bytes'])
        effort = context.get("reasoning_effort") or "medium"
        try:
            answers = form_answers(context)
        except Exception as exc:
            return _blocked(
                context,
                logic + ["Asked grok-4.7 for extra answers from the applicant profile."],
                ["The model call failed: " + str(exc).split("\n")[0]],
                "system_error",
            )
        logic = logic + [
            "Asked grok-4.7 at {} effort for extra answers from the applicant profile.".format(effort),
            "Opened the verified Comeet form in Chrome.",
            "Filled identity fields from the profile and matching extra answers from the model.",
        ]
        payload = {
            'application': application,
            'destination': destination,
            'profile': context['applicant_profile'],
            'answers': answers,
            'package_path': package_path,
            'package_name': name,
            'chrome': chrome,
        }
        process = subprocess.run([node, os.path.join(ROOT, 'adapters', 'browser', 'comeet.cjs')],
                                 input=json.dumps(payload), capture_output=True, text=True, timeout=240)
        if process.returncode:
            # Do not print browser messages, which can contain iframe tokens.
            raise RuntimeError('browser adapter interrupted; inspect the recorded unconfirmed attempt before retrying')
        outcome = json.loads(process.stdout)
        tackled = [
            "Filled {} from {}.".format(field.get("label"), field.get("source") or "unknown")
            for field in outcome.get("fields") or []
            if field.get("value") and field.get("state") in {"prepared", "submitted"}
        ]
        if outcome.get("status") == "Submitted successfully":
            logic.append("Clicked submit once and read the confirmation on the page.")
            tackled.append("Cleared the form through to a visible confirmation.")
        outcome["attempt"] = _attempt(context, logic, list(outcome.get("blockers") or []), tackled)
        return outcome
