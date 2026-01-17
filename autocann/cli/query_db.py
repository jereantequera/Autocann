#!/usr/bin/env python

"""
Small CLI to query the SQLite database.
Useful for debugging and quick analysis.
"""

from __future__ import annotations

import sys
from datetime import datetime

import pytz

from autocann.db import cleanup_old_data, get_aggregated_data, get_database_stats, get_latest_sensor_data

ARGENTINA_TZ = pytz.timezone("America/Argentina/Cordoba")


def print_header(title: str) -> None:
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def show_stats() -> None:
    """Show database statistics."""
    print_header("Database Statistics")
    stats = get_database_stats()

    if not stats:
        print("❌ No statistics available")
        return

    print(f"Database path:    {stats['database_path']}")
    print(f"Database size:    {stats['database_size_mb']} MB")
    print(f"Sensor records:   {stats['sensor_data_count']:,}")
    print(f"Control events:   {stats['control_events_count']:,}")
    print(f"Oldest record:    {stats['oldest_record']}")
    print(f"Newest record:    {stats['newest_record']}")

    if stats.get("sensor_data_count", 0) and stats.get("oldest_record") and stats.get("newest_record"):
        oldest = datetime.strptime(stats["oldest_record"], "%Y-%m-%d %H:%M:%S")
        newest = datetime.strptime(stats["newest_record"], "%Y-%m-%d %H:%M:%S")
        days = (newest - oldest).days
        if days > 0:
            records_per_day = stats["sensor_data_count"] / days
            print(f"Records per day:  {records_per_day:,.0f}")


def show_latest(count: int = 10) -> None:
    """Show latest sensor readings."""
    print_header(f"Latest {count} Sensor Readings")
    data = get_latest_sensor_data(limit=count)

    if not data:
        print("❌ No data available")
        return

    print(f"{'Datetime':<20} {'Temp (°C)':<10} {'Humidity (%)':<12} {'VPD (kPa)':<10}")
    print("-" * 60)

    for record in data:
        print(
            f"{record['datetime']:<20} {record['temperature']:<10.1f} "
            f"{record['humidity']:<12.1f} {record['vpd']:<10.2f}"
        )


def show_daily_summary(days: int = 7) -> None:
    """Show daily summary for the last N days."""
    print_header(f"Daily Summary - Last {days} Days")

    current_time = datetime.now(ARGENTINA_TZ)
    end_timestamp = int(current_time.timestamp())
    start_timestamp = end_timestamp - (days * 24 * 3600)

    data = get_aggregated_data(start_timestamp, end_timestamp, interval_seconds=24 * 3600)

    if not data:
        print("❌ No data available")
        return

    print(
        f"{'Date':<12} {'Avg Temp':<10} {'Min/Max Temp':<15} "
        f"{'Avg Humidity':<12} {'Min/Max Hum':<15} {'Samples':<10}"
    )
    print("-" * 80)

    for record in data:
        date = record["datetime"].split()[0]
        avg_temp = record["temperature"] or 0
        min_temp = record["min_temperature"] or 0
        max_temp = record["max_temperature"] or 0
        avg_hum = record["humidity"] or 0
        min_hum = record["min_humidity"] or 0
        max_hum = record["max_humidity"] or 0
        samples = record["sample_count"]

        print(
            f"{date:<12} {avg_temp:<10.1f} {min_temp:.1f}/{max_temp:.1f}°C{'':<5} "
            f"{avg_hum:<12.1f} {min_hum:.1f}/{max_hum:.1f}%{'':<4} {samples:<10,}"
        )


def show_hourly_today() -> None:
    """Show hourly data for today."""
    print_header("Hourly Data - Today")

    current_time = datetime.now(ARGENTINA_TZ)
    start_of_day = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    start_timestamp = int(start_of_day.timestamp())
    end_timestamp = int(current_time.timestamp())

    data = get_aggregated_data(start_timestamp, end_timestamp, interval_seconds=3600)

    if not data:
        print("❌ No data available for today")
        return

    print(f"{'Hour':<15} {'Avg Temp (°C)':<15} {'Avg Humidity (%)':<18} {'VPD (kPa)':<12} {'Samples':<10}")
    print("-" * 75)

    for record in data:
        hour = record["datetime"].split()[1][:5]
        avg_temp = record["temperature"] or 0
        avg_hum = record["humidity"] or 0
        avg_vpd = record["vpd"] or 0
        samples = record["sample_count"]
        print(f"{hour:<15} {avg_temp:<15.1f} {avg_hum:<18.1f} {avg_vpd:<12.2f} {samples:<10,}")


def cleanup_old(days: int = 90) -> None:
    """Clean up old data."""
    print_header(f"Cleanup Old Data (>{days} days)")

    response = input(f"⚠️  This will delete records older than {days} days. Continue? (yes/no): ")
    if response.lower() != "yes":
        print("❌ Cancelled")
        return

    sensor_deleted, control_deleted = cleanup_old_data(days_to_keep=days)
    print(f"✅ Deleted {sensor_deleted:,} sensor records")
    print(f"✅ Deleted {control_deleted:,} control events")


def search_by_date(date_str: str) -> None:
    """Search data for a specific date (YYYY-MM-DD)."""
    print_header(f"Data for {date_str}")

    try:
        date_naive = datetime.strptime(date_str, "%Y-%m-%d")
        date = ARGENTINA_TZ.localize(date_naive)

        start_timestamp = int(date.timestamp())
        end_timestamp = start_timestamp + (24 * 3600)

        data = get_aggregated_data(start_timestamp, end_timestamp, interval_seconds=3600)

        if not data:
            print(f"❌ No data available for {date_str}")
            return

        print(f"{'Hour':<15} {'Avg Temp (°C)':<15} {'Avg Humidity (%)':<18} {'VPD (kPa)':<12}")
        print("-" * 65)

        for record in data:
            hour = record["datetime"].split()[1][:5]
            avg_temp = record["temperature"] or 0
            avg_hum = record["humidity"] or 0
            avg_vpd = record["vpd"] or 0
            print(f"{hour:<15} {avg_temp:<15.1f} {avg_hum:<18.1f} {avg_vpd:<12.2f}")

    except ValueError:
        print("❌ Invalid date format. Use YYYY-MM-DD (e.g., 2025-10-01)")


def show_help() -> None:
    """Show help message."""
    print_header("Query Database - Help")
    print(
        """
Usage: python -m autocann.cli.query_db [command] [options]

Commands:
  stats              Show database statistics
  latest [N]         Show latest N sensor readings (default: 10)
  daily [N]          Show daily summary for last N days (default: 7)
  today              Show hourly data for today
  date YYYY-MM-DD    Show hourly data for specific date
  cleanup [N]        Delete data older than N days (default: 90)
  help               Show this help message

Examples:
  python -m autocann.cli.query_db stats
  python -m autocann.cli.query_db latest 20
  python -m autocann.cli.query_db daily 30
  python -m autocann.cli.query_db today
  python -m autocann.cli.query_db date 2025-10-01
  python -m autocann.cli.query_db cleanup 60
"""
    )


def main() -> None:
    if len(sys.argv) < 2:
        show_stats()
        print("\n💡 Use 'python -m autocann.cli.query_db help' for more options\n")
        return

    command = sys.argv[1].lower()

    if command == "stats":
        show_stats()
    elif command == "latest":
        count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        show_latest(count)
    elif command == "daily":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        show_daily_summary(days)
    elif command == "today":
        show_hourly_today()
    elif command == "date":
        if len(sys.argv) < 3:
            print("❌ Please provide a date (YYYY-MM-DD)")
            return
        search_by_date(sys.argv[2])
    elif command == "cleanup":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 90
        cleanup_old(days)
    elif command == "help":
        show_help()
    else:
        print(f"❌ Unknown command: {command}")
        print("💡 Use 'python -m autocann.cli.query_db help' for available commands")


if __name__ == "__main__":
    main()

