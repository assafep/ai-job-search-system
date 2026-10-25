"""One event layout for every workflow outcome."""


def attempt_lines(attempt=None):
    """Model, effort, logic, obstacles, and tackled, in that order."""
    attempt = attempt or {}

    def text(value, empty):
        rendered = str(value or "").strip()
        return rendered or empty

    def joined(key):
        items = [str(item).strip() for item in (attempt.get(key) or []) if str(item).strip()]
        return "; ".join(items) if items else "none"

    return [
        "Model: {}.".format(text(attempt.get("model"), "not called")),
        "Effort: {}.".format(text(attempt.get("effort"), "not used")),
        "Logic tried: " + joined("logic"),
        "Obstacles: " + joined("obstacles"),
        "Tackled: " + joined("tackled"),
    ]


def with_attempt(bullets, attempt=None):
    """Outcome note, then the five attempt lines. State changes are printed elsewhere."""
    lines = [str(item) for item in (bullets or []) if str(item).strip()]
    block = attempt_lines(attempt)
    if lines[-len(block):] == block:
        return lines
    return lines + block
