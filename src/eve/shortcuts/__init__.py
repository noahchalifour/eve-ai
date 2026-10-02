"""Learned shortcuts (ENG-296): Eve gets faster at what a member asks for
again and again.

When the same request keeps resolving, through a specialist, to the same
concrete eve-tools call, that resolved call is promoted to a shortcut. Eve
then makes it herself with one `run_shortcut` call instead of a specialist's
whole agent loop. See docs/adr/0021-learned-shortcuts-are-a-table.md.

    allowlist  - the closed set of calls that may ever become a shortcut
    capture    - turns a specialist's inner trace into a fingerprint
    store      - observations, promotion, and the shortcut rows
    tools      - `run_shortcut`
    cli        - `eve-shortcut list|show|revoke`
"""
