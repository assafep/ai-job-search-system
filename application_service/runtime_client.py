"""In-service implementation of the same runtime persistence interface.

Only the Application Service instantiates this bridge. Agents see plain JSON;
all domain mutations still go through the service's repository implementations.
"""
from dataclasses import asdict, is_dataclass


def plain(value):
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, list):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    return value


class RuntimeClient:
    def __init__(self, repository):
        self.repository = repository

    def __getattr__(self, name):
        target = getattr(self.repository, name)
        return lambda *args, **kw: plain(target(*args, **kw))

    def complete_application(self, application_id, reason):
        from .terminal_journal import complete_application
        return plain(complete_application(self.repository, application_id, reason))
