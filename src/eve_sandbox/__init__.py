"""eve-sandbox: executes Eve-authored tool code and holds nothing.

The opposite polarity to eve-tools (ADR 0006): that service holds every
third-party credential and runs only human-written code; this one runs
machine-written code and holds no credential, no cluster identity, and no
network route.

This package imports NOTHING from `eve`. eve_tools reaches into eve.settings
for its own reasons; this must not, because every import is a line of code
that could be tricked into reading something.
"""
