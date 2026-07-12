"""Offline ingest subsystem (technical plan §4).

For the first slice this is just a hand-written seed that publishes a valid
snapshot, so the chat side has real data to retrieve against before any
dataset loader or crawler exists. It writes the snapshot format defined in
``schemas.snapshot_format`` and shares nothing else with ``chat``.
"""
