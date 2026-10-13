ALLOWED_TRANSITIONS = {
    "Bookmarked": {"Ready for Auto-Apply", "Manual Apply Required"},
    "Ready for Auto-Apply": {"Applied", "Manual Apply Required"},
    "Manual Apply Required": {"Applied", "Ready for Auto-Apply"},
    "Applied": {"Interviewing", "Rejected"},
    "Interviewing": {"Interviewing", "Offer", "Rejected"},
    "Rejected": set(),
    "Offer": set(),
}


def validate_transition(current: str, target: str) -> None:
    # Replaying an already-applied transition is a safe no-op. This is
    # distinct from moving between two different lifecycle states and keeps
    # retry behavior consistent across repository implementations.
    if current == target:
        return
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError("invalid transition: {} -> {}".format(current, target))
