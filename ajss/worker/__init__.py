"""The long-running AJSS process.

`python -m ajss.cli run` loads this package. It is the only writer of the
interviews journal. Stage handlers, observers, and the remote read API live
here.
"""
