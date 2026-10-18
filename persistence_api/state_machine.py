ALLOWED_TRANSITIONS = {
    "Replay": {"Bookmarked", "Manual Apply Required", "Rejected", "Duplicate", "Dead Letters Queue"},
    "Bookmarked": {"Ready for Auto-Apply", "Manual Apply Required", "Duplicate", "Dead Letters Queue"},
    "Ready for Auto-Apply": {"Applied", "Manual Apply Required", "Duplicate", "Dead Letters Queue"},
    "Manual Apply Required": {"Applied", "Ready for Auto-Apply", "Duplicate", "Dead Letters Queue"},
    "Applied": {"Interviewing", "Rejected", "Offer", "Dead Letters Queue"},
    "Interviewing": {"Interviewing", "Offer", "Rejected", "Dead Letters Queue"},
    "Dead Letters Queue": {"Replay", "Bookmarked", "Ready for Auto-Apply", "Manual Apply Required", "Applied", "Interviewing", "Duplicate"},
    "Rejected": set(),
    "Offer": set(),
    "Duplicate": set(),
}


def validate_transition(current: str, target: str) -> None:
    # Replaying an already-applied transition is a safe no-op. This is
    # distinct from moving between two different lifecycle states and keeps
    # retry behavior consistent across repository implementations.
    if current == target:
        return
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError("invalid transition: {} -> {}".format(current, target))
