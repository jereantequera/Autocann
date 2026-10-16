"""
VPD score: how much of a period the climate spent inside the target ranges.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from autocann.control.plausible import AND_VALID_ROW
from autocann.control.vpd_math import VPD_RANGES
from autocann.store.connection import _open
from autocann.store.grows import _NO_GROW, ALL_GROWS, _resolve_grow_filter
from autocann.store.schema import ensure_schema
from autocann.time import ARGENTINA_TZ

# ===============================
# Analytics Functions
# ===============================



def _is_all_grows(grow_id) -> bool:
    return isinstance(grow_id, str) and grow_id.strip().lower() == ALL_GROWS


def _grow_identity(grow_id: int) -> Tuple[str, str]:
    """`(name, stage)` for a grow, with usable fallbacks if the row is gone."""
    try:
        ensure_schema()
        conn = _open()
        row = conn.execute(
            "SELECT name, stage FROM grows WHERE id = ?", (grow_id,)).fetchone()
        conn.close()
    except Exception as e:
        print(f"Error reading grow {grow_id}: {e}")
        return "Unknown", "early_veg"
    if row is None:
        return "Unknown", "early_veg"
    return row[0], row[1]


#: Refused by the statistics that score readings against a stage's band.
_ALL_GROWS_IS_NOT_A_GROW = (
    "Estas estadísticas son por cultivo: el score mide tiempo dentro de la "
    "banda de una etapa, y cada cultivo pasó por etapas distintas. Elegí uno."
)


def get_vpd_score(
    days: int = 1,
    grow_id=None,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
) -> Dict:
    """
    Calculate VPD score: percentage of time VPD was in optimal range.
    Returns daily scores for the specified number of days.

    If start_ts and end_ts are provided, uses calendar days within that range.
    Otherwise, uses rolling periods of 24 hours.

    `ALL_GROWS` is refused rather than answered: see
    `_ALL_GROWS_IS_NOT_A_GROW`.
    """
    try:
        if _is_all_grows(grow_id):
            return {"error": _ALL_GROWS_IS_NOT_A_GROW}

        resolved = _resolve_grow_filter(grow_id)
        if resolved is _NO_GROW:
            return {"error": "No active grow found"}
        grow_id = resolved
        _, stage = _grow_identity(grow_id)

        vpd_min, vpd_max = VPD_RANGES.get(stage, (0.6, 1.5))

        ensure_schema()
        conn = _open()
        cursor = conn.cursor()

        current_time = datetime.now(ARGENTINA_TZ)
        
        # Use provided timestamps or calculate from days
        if start_ts is not None and end_ts is not None:
            start_timestamp = start_ts
            end_timestamp = end_ts
            # Calculate calendar days
            start_date = datetime.fromtimestamp(start_ts, ARGENTINA_TZ).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end_date = datetime.fromtimestamp(end_ts, ARGENTINA_TZ)
            days = (end_date.date() - start_date.date()).days + 1
        else:
            end_timestamp = int(current_time.timestamp())
            start_timestamp = end_timestamp - (days * 24 * 3600)
            start_date = None

        daily_scores = []
        
        if start_ts is not None and end_ts is not None:
            # Use calendar days
            current_date = datetime.fromtimestamp(start_ts, ARGENTINA_TZ).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            for _ in range(days):
                day_start = int(current_date.timestamp())
                next_day = current_date + timedelta(days=1)
                day_end = int(next_day.timestamp())
                
                query = """
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN COALESCE(leaf_vpd, vpd) >= ? AND COALESCE(leaf_vpd, vpd) <= ? THEN 1 ELSE 0 END) as in_range
                    FROM sensor_data
                    WHERE timestamp >= ? AND timestamp < ?
                """ + AND_VALID_ROW
                params: List[object] = [vpd_min, vpd_max, day_start, day_end]

                if grow_id is not None:
                    query = query.replace("WHERE", "WHERE grow_id = ? AND")
                    params.insert(2, grow_id)  # Insert after vpd_min, vpd_max

                cursor.execute(query, params)
                row = cursor.fetchone()

                total = row[0] or 0
                in_range = row[1] or 0
                score = round((in_range / total) * 100, 1) if total > 0 else None

                daily_scores.append({
                    "date": current_date.strftime("%Y-%m-%d"),
                    "day_name": current_date.strftime("%A"),
                    "score": score,
                    "samples_total": total,
                    "samples_in_range": in_range,
                })
                current_date = next_day
        else:
            # Use rolling 24-hour periods (original behavior)
            for day_offset in range(days):
                day_start = end_timestamp - ((day_offset + 1) * 24 * 3600)
                day_end = end_timestamp - (day_offset * 24 * 3600)

                query = """
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN COALESCE(leaf_vpd, vpd) >= ? AND COALESCE(leaf_vpd, vpd) <= ? THEN 1 ELSE 0 END) as in_range
                    FROM sensor_data
                    WHERE timestamp >= ? AND timestamp < ?
                """ + AND_VALID_ROW
                params = [vpd_min, vpd_max, day_start, day_end]

                if grow_id is not None:
                    query = query.replace("WHERE", "WHERE grow_id = ? AND")
                    params.insert(2, grow_id)  # Insert after vpd_min, vpd_max

                cursor.execute(query, params)
                row = cursor.fetchone()

                total = row[0] or 0
                in_range = row[1] or 0
                score = round((in_range / total) * 100, 1) if total > 0 else None

                day_date = datetime.fromtimestamp(day_start, ARGENTINA_TZ)
                daily_scores.append({
                    "date": day_date.strftime("%Y-%m-%d"),
                    "day_name": day_date.strftime("%A"),
                    "score": score,
                    "samples_total": total,
                    "samples_in_range": in_range,
                })
            # Reverse to have oldest first
            daily_scores = list(reversed(daily_scores))

        query = """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN COALESCE(leaf_vpd, vpd) >= ? AND COALESCE(leaf_vpd, vpd) <= ? THEN 1 ELSE 0 END) as in_range
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
        """ + AND_VALID_ROW
        params = [vpd_min, vpd_max, start_timestamp, end_timestamp]

        if grow_id is not None:
            query = query.replace("WHERE", "WHERE grow_id = ? AND")
            params.insert(2, grow_id)  # Insert after vpd_min, vpd_max

        cursor.execute(query, params)
        row = cursor.fetchone()
        conn.close()

        total = row[0] or 0
        in_range = row[1] or 0
        overall_score = round((in_range / total) * 100, 1) if total > 0 else None

        return {
            "overall_score": overall_score,
            "period_samples_total": total,
            "period_samples_in_range": in_range,
            "vpd_range": {"min": vpd_min, "max": vpd_max},
            "stage": stage,
            "days": days,
            "daily_scores": daily_scores,  # Already oldest first
        }

    except Exception as e:
        print(f"Error calculating VPD score: {e}")
        return {"error": str(e)}
