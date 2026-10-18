"""
Tools for the simulator that build data rather than run processes.

`history` is pure: it makes the rows of a synthetic grow. `seed` writes them
into a sandbox database through the store.

The rest of the simulator lives here too, and `cli/simulate.py` only runs it:
`model` (the tent), `scenarios` (timelines and what the sensors report),
`sandbox` (the guards on Redis DB, port and data), `processes` (child processes),
`http_client` and `fake_nodes` (the fake sensor and relay nodes).
"""
