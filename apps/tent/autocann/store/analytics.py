"""
Weekly report and anomaly detection.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from autocann.control.anomalies import find_anomalies
from autocann.control.plausible import AND_VALID_ROW
from autocann.store.connection import _open
from autocann.store.grow_stages import grow_vpd_band
from autocann.store.grows import _NO_GROW, _resolve_grow_filter
from autocann.store.history import get_period_summary
from autocann.store.migrations import _parse_local_datetime
from autocann.store.schema import ensure_schema
from autocann.store.vpd_score import _ALL_GROWS_IS_NOT_A_GROW, _grow_identity, _is_all_grows, get_vpd_score
from autocann.time import ARGENTINA_TZ


def get_weekly_report(
    grow_id=None,
) -> Dict:
    """
    Generate a comprehensive weekly report with statistics and insights.

    Scoped to one grow, for the reason in `_ALL_GROWS_IS_NOT_A_GROW`. Note that
    the "previous week" comparison is scoped too, so the first week of a grow
    compares against nothing and reports no trend — which is the truth, rather
    than a trend measured against the tent standing empty.
    """
    try:
        if _is_all_grows(grow_id):
            return {"error": _ALL_GROWS_IS_NOT_A_GROW}

        resolved = _resolve_grow_filter(grow_id)
        if resolved is _NO_GROW:
            return {"error": "No active grow found"}
        grow_id = resolved
        grow_name, stage = _grow_identity(grow_id)

        current_time = datetime.now(ARGENTINA_TZ)
        
        # Calculate week boundaries (Monday 00:00 to Sunday 23:59:59 or now)
        days_since_monday = current_time.weekday()  # 0=Monday, 6=Sunday
        week_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = week_start - timedelta(days=days_since_monday)
        
        start_timestamp = int(week_start.timestamp())
        end_timestamp = int(current_time.timestamp())

        summary = get_period_summary(start_timestamp, end_timestamp, grow_id)

        # Get VPD score for this week (using calendar days)
        vpd_score = get_vpd_score(grow_id=grow_id, start_ts=start_timestamp, end_ts=end_timestamp)

        ensure_schema()
        conn = _open()
        cursor = conn.cursor()

        # Get hourly distribution (what hours have best/worst VPD)
        vpd_min, vpd_max = grow_vpd_band(grow_id, stage)

        query = """
            SELECT 
                CAST(strftime('%H', datetime) AS INTEGER) as hour,
                COUNT(*) as total,
                AVG(temperature) as avg_temp,
                AVG(humidity) as avg_humidity,
                AVG(vpd) as avg_vpd,
                SUM(CASE WHEN COALESCE(leaf_vpd, vpd) >= ? AND COALESCE(leaf_vpd, vpd) <= ? THEN 1 ELSE 0 END) as in_range
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
        """ + AND_VALID_ROW
        params: List[object] = [vpd_min, vpd_max, start_timestamp, end_timestamp]

        if grow_id is not None:
            query = query.replace("WHERE", "WHERE grow_id = ? AND")
            params.insert(2, grow_id)  # Insert after vpd_min, vpd_max

        query += " GROUP BY hour ORDER BY hour"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        hourly_stats = []
        best_hour = None
        worst_hour = None
        best_score = -1
        worst_score = 101

        for row in rows:
            hour = row[0]
            total = row[1]
            in_range = row[5]
            score = round((in_range / total) * 100, 1) if total > 0 else 0

            hourly_stats.append({
                "hour": hour,
                "hour_label": f"{hour:02d}:00",
                "avg_temp": round(row[2], 1) if row[2] is not None else None,
                "avg_humidity": round(row[3], 1) if row[3] is not None else None,
                "avg_vpd": round(row[4], 2) if row[4] is not None else None,
                "vpd_score": score,
                "samples": total,
            })

            if score > best_score:
                best_score = score
                best_hour = hour
            if score < worst_score:
                worst_score = score
                worst_hour = hour

        # Compare with previous week (previous Monday to Sunday)
        prev_week_start = week_start - timedelta(days=7)
        prev_week_end = week_start  # Sunday 23:59:59 of previous week
        prev_start = int(prev_week_start.timestamp())
        prev_end = int(prev_week_end.timestamp())

        prev_summary = get_period_summary(prev_start, prev_end, grow_id)
        prev_vpd_score = get_vpd_score(grow_id=grow_id, start_ts=prev_start, end_ts=prev_end)

        conn.close()

        temp_trend = None
        humidity_trend = None
        vpd_trend = None

        if summary.get("temperature", {}).get("avg") and prev_summary.get("temperature", {}).get("avg"):
            temp_trend = round(summary["temperature"]["avg"] - prev_summary["temperature"]["avg"], 1)
        if summary.get("humidity", {}).get("avg") and prev_summary.get("humidity", {}).get("avg"):
            humidity_trend = round(summary["humidity"]["avg"] - prev_summary["humidity"]["avg"], 1)
        if vpd_score.get("overall_score") and prev_vpd_score.get("overall_score"):
            vpd_trend = round(vpd_score["overall_score"] - prev_vpd_score["overall_score"], 1)

        return {
            "grow_name": grow_name,
            "stage": stage,
            "report_period": {
                "start": datetime.fromtimestamp(start_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d"),
                "end": datetime.fromtimestamp(end_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d"),
            },
            "summary": {
                "temperature": summary.get("temperature", {}),
                "humidity": summary.get("humidity", {}),
                "vpd": summary.get("vpd", {}),
                "sample_count": summary.get("sample_count", 0),
            },
            "vpd_score": {
                "overall": vpd_score.get("overall_score"),
                "daily": vpd_score.get("daily_scores", []),
                "range": vpd_score.get("vpd_range", {}),
            },
            "trends": {
                "temperature": temp_trend,
                "humidity": humidity_trend,
                "vpd_score": vpd_trend,
            },
            "insights": {
                "best_hour": best_hour,
                "worst_hour": worst_hour,
                "best_hour_score": best_score if best_hour is not None else None,
                "worst_hour_score": worst_score if worst_hour is not None else None,
            },
            "hourly_distribution": hourly_stats,
        }

    except Exception as e:
        print(f"Error generating weekly report: {e}")
        return {"error": str(e)}


def detect_anomalies(
    hours: int = 24,
    grow_id=None,
) -> Dict:
    """
    The stored rows of the last `hours`, run through `control.anomalies`.

    Scoped like the rest, and with no active grow it reports no anomalies at
    all, under `status: "no_grow"`, rather than falling back to every grow.
    Reading a closed grow's last rows as "the tent is fine" would be wrong, but
    so is calling the empty history an outage: without a grow nothing is
    written by design, and that is a state for the dashboard to announce, not
    a fault to raise in red.
    """
    try:
        end_timestamp = int(datetime.now(ARGENTINA_TZ).timestamp())
        start_timestamp = end_timestamp - (hours * 3600)

        resolved = _resolve_grow_filter(grow_id)
        if resolved is _NO_GROW:
            return {
                "status": "no_grow",
                "no_active_grow": True,
                "anomalies": [],
                "warnings": [],
            }
        grow_id = resolved

        ensure_schema()
        conn = _open(row_factory=True)
        cursor = conn.cursor()

        query = """
            SELECT timestamp, datetime, temperature, humidity
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
        """
        params: List[object] = [start_timestamp, end_timestamp]
        grow_started_at = None
        if grow_id is not None:
            query = query.replace("WHERE", "WHERE grow_id = ? AND")
            params.insert(0, grow_id)
            start_row = cursor.execute("SELECT start_date FROM grows WHERE id = ?", (grow_id,)).fetchone()
            grow_started_at = _parse_local_datetime(start_row[0]) if start_row else None
        query += " ORDER BY timestamp ASC"
        rows = [dict(row) for row in cursor.execute(query, params).fetchall()]
        conn.close()

        report = find_anomalies(rows, end_timestamp, hours=hours, grow_started_at=grow_started_at)
        report["checked_period"] = {
            "hours": hours,
            "samples_checked": len(rows),
            "start": datetime.fromtimestamp(start_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "end": datetime.fromtimestamp(end_timestamp, ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }
        return report

    except Exception as e:
        print(f"Error detecting anomalies: {e}")
        return {"error": str(e), "status": "error"}
