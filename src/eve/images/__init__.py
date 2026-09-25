"""Images through Eve (EVE-21, spec docs/superpowers/specs/2026-09-22-eve-images-design.md).

An image is a row in `eve_image` addressed by an opaque UUID. Nothing else in
the system carries pixels: not state, not checkpoints, not tool arguments,
not UI frames. `hydrate` (model boundary), `app` (the phone's proxy) and
`immich` (the cache fill) are the only places bytes move.
"""
