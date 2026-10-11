from __future__ import annotations

import sqlite3

import pytest

from autocann.cli.compact_db import analyse, compact


def timeline(path):
    """The relay timeline: one entry per actual state change."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    for (kind,) in conn.execute("SELECT DISTINCT event_type FROM control_events"):
        rows = conn.execute(
            "SELECT timestamp, value FROM control_events WHERE event_type=?"
            " ORDER BY timestamp, id", (kind,)).fetchall()
        series, previous = [], None
        for ts, value in rows:
            on = value == "on"
            if previous is None or on != previous:
                series.append((ts, on))
            previous = on
        out[kind] = series
    conn.close()
    return out


@pytest.fixture
def bloated(tmp_path):
    """A database shaped like production: a few transitions, many repetitions."""
    path = tmp_path / "bloated.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE control_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp INTEGER NOT NULL, datetime TEXT NOT NULL,
          event_type TEXT NOT NULL, value TEXT NOT NULL);
        CREATE TABLE sensor_data (id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp INTEGER NOT NULL, temperature REAL, humidity REAL);
        """
    )
    ts = 1_700_000_000
    # 20 real transitions per output, each repeated 50 times like the old loop did.
    for kind in ("humidity_up", "humidity_down"):
        for i in range(20):
            value = "on" if i % 2 == 0 else "off"
            for _ in range(50):
                conn.execute(
                    "INSERT INTO control_events (timestamp, datetime, event_type, value)"
                    " VALUES (?, '', ?, ?)", (ts, kind, value))
                ts += 3
    for i in range(200):
        conn.execute("INSERT INTO sensor_data (timestamp, temperature, humidity)"
                     " VALUES (?, ?, ?)", (1_700_000_000 + i * 300, 24.0 + i % 3, 50.0))
    conn.commit()
    conn.close()
    return path


def test_it_counts_what_it_would_remove_before_touching_anything(bloated):
    conn = sqlite3.connect(f"file:{bloated}?mode=ro", uri=True)
    stats = analyse(conn)
    conn.close()
    assert stats["total"] == 2 * 20 * 50
    assert stats["keep"] == 2 * 20          # one row per transition, per output
    assert stats["redundant"] == stats["total"] - stats["keep"]


def test_the_relay_timeline_survives_bit_for_bit(bloated):
    """
    The whole point: this compacts rather than purges. Every state change has to
    still be there afterwards, because that history is the baseline the new
    control loop gets compared against.
    """
    before = timeline(bloated)
    compact(bloated, backup=False)
    assert timeline(bloated) == before


def test_sensor_history_is_never_touched(bloated):
    conn = sqlite3.connect(f"file:{bloated}?mode=ro", uri=True)
    before = conn.execute(
        "SELECT COUNT(*), SUM(temperature), SUM(humidity) FROM sensor_data").fetchone()
    conn.close()

    compact(bloated, backup=False)

    conn = sqlite3.connect(f"file:{bloated}?mode=ro", uri=True)
    after = conn.execute(
        "SELECT COUNT(*), SUM(temperature), SUM(humidity) FROM sensor_data").fetchone()
    conn.close()
    assert after == before


def test_the_database_stays_valid(bloated):
    compact(bloated, backup=False)
    conn = sqlite3.connect(f"file:{bloated}?mode=ro", uri=True)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_running_it_twice_removes_nothing_the_second_time(bloated):
    assert compact(bloated, backup=False) > 0
    assert compact(bloated, backup=False) == 0


def test_the_first_event_of_each_output_is_kept_so_the_series_has_a_start(bloated):
    compact(bloated, backup=False)
    conn = sqlite3.connect(f"file:{bloated}?mode=ro", uri=True)
    for kind in ("humidity_up", "humidity_down"):
        first = conn.execute(
            "SELECT value FROM control_events WHERE event_type=?"
            " ORDER BY timestamp, id LIMIT 1", (kind,)).fetchone()
        assert first[0] == "on"
    conn.close()


def test_a_backup_is_written_alongside_the_database(bloated):
    compact(bloated, backup=True)
    backups = list(bloated.parent.glob("*antes-de-compactar*"))
    assert len(backups) == 1

    conn = sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True)
    assert conn.execute("SELECT COUNT(*) FROM control_events").fetchone()[0] == 2000
    conn.close()


def test_an_already_compact_database_is_left_alone(tmp_path):
    path = tmp_path / "clean.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE control_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp INTEGER NOT NULL, datetime TEXT NOT NULL,
          event_type TEXT NOT NULL, value TEXT NOT NULL);
        CREATE TABLE sensor_data (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER);
        INSERT INTO control_events (timestamp, datetime, event_type, value)
          VALUES (1, '', 'humidity_up', 'on'), (2, '', 'humidity_up', 'off');
        """
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert analyse(conn)["redundant"] == 0
    conn.close()
