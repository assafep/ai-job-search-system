"""Validation for paths relative to the configured Drive artifact root."""

import posixpath


def normalize_file_path(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("file path must be a non-empty string")
    value = value.replace("\\", "/").strip()
    if value.startswith("/"):
        raise ValueError("file path must be relative to the Drive root")
    parts = [part for part in value.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("file path must not escape the Drive root")
    normalized = posixpath.normpath("/".join(parts))
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise ValueError("file path must not escape the Drive root")
    return normalized
