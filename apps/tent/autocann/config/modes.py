"""
Pause and control modes: the Redis and database keys that hold them, and the mode names.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Pause
# ---------------------------------------------------------------------------

#: Redis key mirroring the pause.
#:
#: Holds the timestamp the pause *started*, and has no TTL. It used to hold a
#: deadline that expired on its own after eight hours, on the argument that a
#: flag someone forgets leaves the tent uncontrolled with nothing to say so.
#: The argument was right about the failure and wrong about the fix: a pause
#: that resumes by itself is worse, because it energises relays at a moment
#: nobody chose - which, if the pause was taken to rewire something, is while
#: somebody's hands are in there. What answers the original worry is that the
#: state is now impossible to miss: the header says it, in amber, on every view,
#: for as long as it lasts.
TEST_MODE_KEY = "test_mode_since"

#: Redis key holding what the operator asked each output to do while paused,
#: as {name: bool}. Deliberately *not* the manual-override keys the automatic
#: path reads: those would still be sitting there when the pause ends, and
#: would then quietly override real control decisions.
TEST_MODE_OUTPUTS_KEY = "test_mode_outputs"

# ---------------------------------------------------------------------------
# Control modes
# ---------------------------------------------------------------------------

#: The loop is deciding and the history is being written. The normal state.
MODE_ACTIVE = "active"

#: The loop decides nothing and the history is written anyway.
#:
#: There used to be two states, and "no decidas" and "no guardes" were the same
#: code path - the paused branch `continue`s before the accumulator is ever
#: reached. That is right for bench work, where somebody is breathing on a
#: sensor and forcing relays by hand, and recording it would put garbage in the
#: history that later reads as real tent behaviour.
#:
#: It is exactly wrong for setup, which is the other reason a tent sits
#: uncontrolled: before the seeds are even germinated, what the room does on
#: its own is the single most useful thing to record. How far it swings
#: overnight with nothing running, whether the two sensors agree, what the
#: humidity settles at - that is the baseline you want *before* there are
#: plants, and the tent was throwing it away.
#:
#: So: nothing decides, nothing switches, everything is recorded.
MODE_MONITOR = "monitor"

#: The loop decides nothing, nothing is recorded, and the relays follow what is
#: asked by hand. For working on the installation.
MODE_PAUSED = "paused"

#: Every mode, for validating what arrives over the API.
CONTROL_MODES = (MODE_ACTIVE, MODE_MONITOR, MODE_PAUSED)

#: The modes in which the control loop is not in charge.
UNCONTROLLED_MODES = (MODE_MONITOR, MODE_PAUSED)

#: Settings key mirroring the mode into the database.
#:
#: Redis stays the fast path - the loop reads it every cycle - but it is not the
#: truth any more. Redis is memory: a restart of it, or of the Raspberry, used
#: to mean the tent silently went back to being controlled. Someone who paused
#: it does not expect a reboot to undo that, so the database carries the state
#: and the loop restores the mirror from it.
#:
#: Holds `{"active": bool, "since": int, "mode": str}`. A record written before
#: monitoring existed has no `mode` and must be read as a pause: that is what
#: every one of them meant, and guessing the other way would hand a tent
#: somebody deliberately stopped back to the controller on the deploy that
#: added the third state.
TEST_MODE_SETTING = "paused"
