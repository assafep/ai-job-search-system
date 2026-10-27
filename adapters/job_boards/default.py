"""Generic structured HTML retrieval for unrecognized job boards."""

from .interface import JobBoardAdapter


class DefaultJobBoardAdapter(JobBoardAdapter):
    provider_name = 'default'
