"""Side effects proposed by a worker and applied only by the orchestrator.

Workers may read and compute. They attach effect objects under ``_effects``.
The orchestrator strips that key before validation and before the event is
stored, runs each effect while it holds the writer lock, then routes the
resulting outcome. A duplicate reservation replaces the outcome and skips
every effect after it.
"""
EFFECTS = "_effects"


class ReplacedOutcome:
    def __init__(self, payload):
        self.payload = payload


def attach(payload, *effects):
    payload = dict(payload)
    payload[EFFECTS] = [*(payload.get(EFFECTS) or ()), *effects]
    return payload


def apply_effects(payload, resources):
    """Run effects in order. Return the outcome with ``_effects`` removed."""
    payload = dict(payload)
    effects = list(payload.pop(EFFECTS, ()) or ())
    data = dict(payload.get("data") or {})
    payload["data"] = data
    for effect in effects:
        result = effect.run(resources, data)
        if isinstance(result, ReplacedOutcome):
            replacement = dict(result.payload)
            replacement.pop(EFFECTS, None)
            if payload.get("attempt_id"):
                replacement["attempt_id"] = payload["attempt_id"]
            return replacement
        if isinstance(result, dict):
            data.update(result)
            payload["data"] = data
    return payload


class ReserveIdentity:
    def __init__(self, item_id, company, role, url="", source_id=""):
        self.item_id = item_id
        self.company = company
        self.role = role
        self.url = url
        self.source_id = source_id

    def run(self, resources, data=None):
        other = resources["index"].reserve_identity(
            self.item_id, self.company, self.role, self.url, self.source_id)
        if not other:
            return None
        return ReplacedOutcome({
            "agent": "bookmark_discovered",
            "item_id": self.item_id,
            "code": "DUPLICATE",
            "execution_status": "SUCCESS",
            "summary": "Known posting",
            "data": {"original_item_id": other},
        })


class SaveArtifact:
    def __init__(self, path, content, mime, key, into=None):
        self.path = path
        self.content = content
        self.mime = mime
        self.key = key
        self.into = into

    def run(self, resources, data=None):
        store = resources.get("store")
        if store is None:
            raise RuntimeError("Artifact store is not configured")
        saved = store.save_path(self.path, self.content, self.mime, self.key) or {}
        if self.into:
            return {self.into: saved.get("id")}
        return None


class SubmitPrepared:
    """The existing submission state machine, run while the orchestrator holds the lock."""

    def __init__(self, item, strategies, strong_agent=None):
        self.item = item
        self.strategies = tuple(strategies)
        self.strong_agent = strong_agent

    def run(self, resources, data=None):
        from ajss.submission.processing import submit_prepared
        result = submit_prepared(
            self.item, resources["index"], self.strategies, strong_agent=self.strong_agent)
        return ReplacedOutcome(result)


class RememberCheckpoint:
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def run(self, resources, data=None):
        value = self.value(data or {}) if callable(self.value) else self.value
        resources["index"].checkpoint(self.name, value)
        return None


class SendReply:
    def __init__(self, reply, email, match):
        self.reply = reply
        self.email = email
        self.match = match

    def run(self, resources, data=None):
        self.reply(self.email, self.match)
        return None


class SaveResearch:
    def __init__(self, item_id, title, report, create_research_tab):
        self.item_id = item_id
        self.title = title
        self.report = report
        self.create_research_tab = create_research_tab

    def run(self, resources, data=None):
        tab_id = self.create_research_tab(self.item_id, self.title, self.report)
        return {"research_tab_id": tab_id}
