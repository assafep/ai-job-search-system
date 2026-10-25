ALLOWED_TRANSITIONS = {
    "Discovered": {"Bookmarked", "Manual Apply Required", "Duplicate", "Dead Letters Queue"},
    "Bookmarked": {"Validated", "Invalidated", "Prepared", "Manual Apply Required", "Duplicate", "Dead Letters Queue"},
    "Validated": {"Prepared", "Manual Apply Required", "Duplicate", "Dead Letters Queue"},
    "Invalidated": {"Bookmarked"},
    "Prepared": {"Applied", "Interviewing", "Rejected", "Offer", "Manual Apply Required", "Duplicate", "Dead Letters Queue"},
    "Manual Apply Required": {"Applied", "Interviewing", "Rejected", "Offer", "Prepared", "Duplicate", "Dead Letters Queue"},
    "Applied": {"Interviewing", "Rejected", "Offer", "Dead Letters Queue"},
    "Interviewing": {"Interviewing", "Offer", "Rejected", "Dead Letters Queue"},
    "Dead Letters Queue": {"Discovered", "Bookmarked", "Validated", "Prepared", "Manual Apply Required", "Applied", "Interviewing", "Duplicate"},
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
