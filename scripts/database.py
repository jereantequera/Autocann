#!/usr/bin/env python

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytz

# Database file location
DB_FILE = Path(__file__).parent.parent / "data" / "autocann.db"

# Argentina timezone
ARGENTINA_TZ = pytz.timezone('America/Argentina/Buenos_Aires')


def init_database():
    """
    Initialize the database and create tables if they don't exist.
    """
    # Create data directory if it doesn't exist
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Create sensor_data table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            datetime TEXT NOT NULL,
            temperature REAL NOT NULL,
            humidity REAL NOT NULL,
            vpd REAL NOT NULL,
            outside_temperature REAL NOT NULL,
            outside_humidity REAL NOT NULL,
            leaf_temperature REAL,
            leaf_vpd REAL,
            target_humidity REAL
        )
    """)
    
    # Create index on timestamp for faster queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_timestamp 
        ON sensor_data(timestamp)
    """)
    
    # Create control_events table for tracking humidity/ventilation changes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS control_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            datetime TEXT NOT NULL,
            event_type TEXT NOT NULL,
            value TEXT NOT NULL
        )
    """)
    
    # Create index on timestamp for control events
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_control_timestamp 
        ON control_events(timestamp)
    """)
    
    conn.commit()
    conn.close()


def store_sensor_sample(sensor_data: Dict) -> bool:
    """
    Store a single sensor reading in the database.
    
    Parameters:
    - sensor_data: Dictionary containing sensor readings
    
    Returns:
    - True if successful, False otherwise
    """
    try:
        argentina_tz = pytz.timezone('America/Argentina/Buenos_Aires')
        current_time = datetime.now(argentina_tz)
        current_timestamp = int(current_time.timestamp())
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO sensor_data (
                timestamp, datetime, temperature, humidity, vpd,
                outside_temperature, outside_humidity,
                leaf_temperature, leaf_vpd, target_humidity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            current_timestamp,
            current_time.strftime('%Y-%m-%d %H:%M:%S'),
            sensor_data.get('temperature'),
            sensor_data.get('humidity'),
            sensor_data.get('vpd'),
            sensor_data.get('outside_temperature'),
            sensor_data.get('outside_humidity'),
            sensor_data.get('leaf_temperature'),
            sensor_data.get('leaf_vpd'),
            sensor_data.get('target_humidity')
        ))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error storing sensor sample: {e}")
        return False


def store_control_event(event_type: str, value: str) -> bool:
    """
    Store a control event (humidity up/down, ventilation on/off).
    
    Parameters:
    - event_type: Type of event (humidity_up, humidity_down, ventilation)
    - value: Value of the event (on, off, true, false)
    
    Returns:
    - True if successful, False otherwise
    """
    try:
        argentina_tz = pytz.timezone('America/Argentina/Buenos_Aires')
        current_time = datetime.now(argentina_tz)
        current_timestamp = int(current_time.timestamp())
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO control_events (timestamp, datetime, event_type, value)
            VALUES (?, ?, ?, ?)
        """, (
            current_timestamp,
            current_time.strftime('%Y-%m-%d %H:%M:%S'),
            event_type,
            value
        ))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error storing control event: {e}")
        return False


def get_sensor_data_range(
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    limit: Optional[int] = None
) -> List[Dict]:
    """
    Get sensor data within a time range.
    
    Parameters:
    - start_timestamp: Start of time range (unix timestamp)
    - end_timestamp: End of time range (unix timestamp)
    - limit: Maximum number of records to return
    
    Returns:
    - List of sensor data dictionaries
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM sensor_data WHERE 1=1"
        params = []
        
        if start_timestamp is not None:
            query += " AND timestamp >= ?"
            params.append(start_timestamp)
        
        if end_timestamp is not None:
            query += " AND timestamp <= ?"
            params.append(end_timestamp)
        
        query += " ORDER BY timestamp DESC"
        
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        result = [dict(row) for row in rows]
        conn.close()
        
        return result
    except Exception as e:
        print(f"Error getting sensor data range: {e}")
        return []


def get_aggregated_data(
    start_timestamp: int,
    end_timestamp: int,
    interval_seconds: int = 3600
) -> List[Dict]:
    """
    Get aggregated (averaged) sensor data for a time range.
    
    Parameters:
    - start_timestamp: Start of time range (unix timestamp)
    - end_timestamp: End of time range (unix timestamp)
    - interval_seconds: Aggregation interval in seconds (default: 1 hour)
    
    Returns:
    - List of aggregated data points
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Use SQLite's integer division to group by intervals
        cursor.execute("""
            SELECT 
                (timestamp / ?) * ? as interval_start,
                AVG(temperature) as avg_temperature,
                AVG(humidity) as avg_humidity,
                AVG(vpd) as avg_vpd,
                AVG(outside_temperature) as avg_outside_temperature,
                AVG(outside_humidity) as avg_outside_humidity,
                AVG(leaf_temperature) as avg_leaf_temperature,
                AVG(leaf_vpd) as avg_leaf_vpd,
                MIN(temperature) as min_temperature,
                MAX(temperature) as max_temperature,
                MIN(humidity) as min_humidity,
                MAX(humidity) as max_humidity,
                COUNT(*) as sample_count
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp <= ?
            GROUP BY interval_start
            ORDER BY interval_start ASC
        """, (interval_seconds, interval_seconds, start_timestamp, end_timestamp))
        
        rows = cursor.fetchall()
        
        result = []
        for row in rows:
            data_point = {
                'timestamp': row['interval_start'],
                'datetime': datetime.fromtimestamp(row['interval_start'], ARGENTINA_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                'temperature': round(row['avg_temperature'], 2) if row['avg_temperature'] else None,
                'humidity': round(row['avg_humidity'], 2) if row['avg_humidity'] else None,
                'vpd': round(row['avg_vpd'], 2) if row['avg_vpd'] else None,
                'outside_temperature': round(row['avg_outside_temperature'], 2) if row['avg_outside_temperature'] else None,
                'outside_humidity': round(row['avg_outside_humidity'], 2) if row['avg_outside_humidity'] else None,
                'leaf_temperature': round(row['avg_leaf_temperature'], 2) if row['avg_leaf_temperature'] else None,
                'leaf_vpd': round(row['avg_leaf_vpd'], 2) if row['avg_leaf_vpd'] else None,
                'min_temperature': round(row['min_temperature'], 2) if row['min_temperature'] else None,
                'max_temperature': round(row['max_temperature'], 2) if row['max_temperature'] else None,
                'min_humidity': round(row['min_humidity'], 2) if row['min_humidity'] else None,
                'max_humidity': round(row['max_humidity'], 2) if row['max_humidity'] else None,
                'sample_count': row['sample_count']
            }
            result.append(data_point)
        
        conn.close()
        return result
    except Exception as e:
        print(f"Error getting aggregated data: {e}")
        return []


def get_latest_sensor_data(limit: int = 100) -> List[Dict]:
    """
    Get the most recent sensor readings.
    
    Parameters:
    - limit: Number of readings to return (default: 100)
    
    Returns:
    - List of sensor data dictionaries
    """
    return get_sensor_data_range(limit=limit)


def cleanup_old_data(days_to_keep: int = 90) -> Tuple[int, int]:
    """
    Remove sensor data older than specified days.
    
    Parameters:
    - days_to_keep: Number of days of data to keep (default: 90)
    
    Returns:
    - Tuple of (sensor_records_deleted, control_events_deleted)
    """
    try:
        argentina_tz = pytz.timezone('America/Argentina/Buenos_Aires')
        current_time = datetime.now(argentina_tz)
        cutoff_timestamp = int(current_time.timestamp()) - (days_to_keep * 24 * 3600)
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Delete old sensor data
        cursor.execute("DELETE FROM sensor_data WHERE timestamp < ?", (cutoff_timestamp,))
        sensor_deleted = cursor.rowcount
        
        # Delete old control events
        cursor.execute("DELETE FROM control_events WHERE timestamp < ?", (cutoff_timestamp,))
        control_deleted = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        return (sensor_deleted, control_deleted)
    except Exception as e:
        print(f"Error cleaning up old data: {e}")
        return (0, 0)


def get_database_stats() -> Dict:
    """
    Get statistics about the database.
    
    Returns:
    - Dictionary with database statistics
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Get sensor data stats
        cursor.execute("SELECT COUNT(*) FROM sensor_data")
        sensor_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM sensor_data")
        min_ts, max_ts = cursor.fetchone()
        
        # Get control events stats
        cursor.execute("SELECT COUNT(*) FROM control_events")
        control_count = cursor.fetchone()[0]
        
        # Get database file size
        db_size_bytes = DB_FILE.stat().st_size if DB_FILE.exists() else 0
        db_size_mb = round(db_size_bytes / (1024 * 1024), 2)
        
        conn.close()
        
        result = {
            'sensor_data_count': sensor_count,
            'control_events_count': control_count,
            'oldest_record': datetime.fromtimestamp(min_ts, ARGENTINA_TZ).strftime('%Y-%m-%d %H:%M:%S') if min_ts else None,
            'newest_record': datetime.fromtimestamp(max_ts, ARGENTINA_TZ).strftime('%Y-%m-%d %H:%M:%S') if max_ts else None,
            'database_size_mb': db_size_mb,
            'database_path': str(DB_FILE)
        }
        
        return result
    except Exception as e:
        print(f"Error getting database stats: {e}")
        return {}


# Initialize database when module is imported
init_database()

