"""
Timing constants shared by the control loop and the anomaly detector.

Kept apart from the loop on purpose: `control/anomalies.py` needs the save
interval to know when a fresh grow may still be waiting for its first sample,
and importing it from `cli/vpd.py` would pull the whole loop into the detector.
"""

#: How often the loop persists a sample to the database. The detector reads it
#: as the cadence of the stored rows, and the loop saves on it.
SAMPLE_SAVE_INTERVAL_SECONDS = 300
