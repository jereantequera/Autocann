"""
The active grow's stage bands, as the control loop reads them.

The loop reads a grow's stages on every stage check and holds the tent to the
band of the stage it is in. The last good copy stays in memory: a database
that fails mid-run keeps the tent on the band it was already following.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from autocann.store.grow_stages import stage_table

log = logging.getLogger(__name__)


class StageBand:
    """`key -> stage row` of the active grow, refreshed by the loop, last good copy kept."""

    def __init__(self) -> None:
        self._table: Dict[str, Dict[str, Any]] = {}
        self._failing = False

    def refresh(self, grow_id: int) -> Dict[str, Dict[str, Any]]:
        """
        Re-read the grow's stages and return the table to use. A failed read, or
        an empty one, keeps the last good table; the failure is logged once.
        `stage_table` already answers with the built-in profile for a grow with
        no snapshot.
        """
        try:
            table = stage_table(grow_id)
        except Exception as error:
            if not self._failing:
                log.warning("Cannot read the stages of grow %s, keeping the last good copy: %s",
                            grow_id, error)
            self._failing = True
            return self._table
        self._failing = False
        if table:
            self._table = table
        return self._table

    def get(self, stage: str) -> Optional[Dict[str, Any]]:
        """The stage's row in the last good table, or None for an unknown key."""
        return self._table.get(stage)
