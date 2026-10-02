"""General-purpose assistant tools (ENG-372): the things any assistant is
expected to do that are not a household domain - look something up, read a
link, do exact arithmetic, convert a time, glance at the calendar or the
weather, set a reminder, keep a list.

Every tool here is top-level on Eve rather than a specialist: each is one
deterministic call with no inner reasoning loop, which is the case ADR 0001's
specialist pattern does not need.
"""
