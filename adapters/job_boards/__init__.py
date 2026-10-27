"""Job-board capabilities and the service-facing execution framework."""

from .interface import (
    AdapterResult, BookmarkContext, DiscoveryContext, JobBoardAdapter,
    SubmissionContext, UnsupportedOperation,
)
from .default import DefaultJobBoardAdapter
from .framework import AdapterFramework, get_framework, fetch_posting_details

__all__ = [
    'AdapterResult', 'BookmarkContext', 'DiscoveryContext', 'JobBoardAdapter',
    'SubmissionContext', 'UnsupportedOperation', 'DefaultJobBoardAdapter',
    'AdapterFramework', 'get_framework', 'fetch_posting_details',
]
