#!/usr/bin/env python

from __future__ import annotations

import json
import sys
from datetime import datetime
from time import sleep

import adafruit_dht
import board
import gpiozero
import pytz
import redis

from autocann.config import gpio_pins_from_env, redis_config_from_env
from autocann.control.vpd_math import (calculate_target_humidity,
                                       calculate_vpd, vpd_is_in_range)
from autocann.db import (get_active_grow, store_control_event,
                         store_sensor_sample)

_pins = gpio_pins_from_env()
HUMIDITY_CONTROL_PIN_UP = _pins.humidity_up
HUMIDITY_CONTROL_PIN_DOWN = _pins.humidity_down

# DHT22 sensor GPIO pins
DHT22_INDOOR_PIN = 4
DHT22_OUTDOOR_PIN = 13

_redis_cfg = redis_config_from_env()
redis_client = redis.Redis(host=_redis_cfg.host, port=_redis_cfg.port, db=_redis_cfg.db)

# Sensor globals (initialized by check_and_init_sensors)
dht22_in = None
dht22_out = None


def get_board_pin(gpio_num: int):
    """
    Map GPIO number to board pin object.
    """
    gpio_to_board = {
        4: board.D4,
        13: board.D13,
    }
    return gpio_to_board.get(gpio_num)


def init_dht22_sensors() -> bool:
    """
    Initialize both DHT22 sensors.
    Returns True if both sensors were initialized, False otherwise.
    """
    global dht22_in, dht22_out

    ok = True

    # Initialize indoor sensor (GPIO 4)
    try:
        if dht22_in is not None:
            try:
                dht22_in.exit()
            except Exception:
                pass
        pin = get_board_pin(DHT22_INDOOR_PIN)
        dht22_in = adafruit_dht.DHT22(pin, use_pulseio=False)
        print(f"✅ DHT22 indoor initialized on GPIO {DHT22_INDOOR_PIN}")
    except Exception as e:
        print(f"❌ DHT22 indoor init failed on GPIO {DHT22_INDOOR_PIN}: {e}")
        dht22_in = None
        ok = False

    # Initialize outdoor sensor (GPIO 13)
    try:
        if dht22_out is not None:
            try:
                dht22_out.exit()
            except Exception:
                pass
        pin = get_board_pin(DHT22_OUTDOOR_PIN)
        dht22_out = adafruit_dht.DHT22(pin, use_pulseio=False)
        print(f"✅ DHT22 outdoor initialized on GPIO {DHT22_OUTDOOR_PIN}")
    except Exception as e:
        print(f"❌ DHT22 outdoor init failed on GPIO {DHT22_OUTDOOR_PIN}: {e}")
        dht22_out = None
        ok = False

    return ok


def check_esp32_indoor_available() -> bool:
    """
    Check if ESP32 indoor sensor data is available and fresh.
    """
    try:
        data = redis_client.get("esp32_indoor")
        if data is None:
            return False

        sensor_data = json.loads(data)
        timestamp = sensor_data.get("timestamp", 0)
        current_time = int(datetime.now(pytz.timezone("America/Argentina/Buenos_Aires")).timestamp())
        age = current_time - timestamp

        # Consider data fresh if less than 60 seconds old
        return age <= 60
    except Exception:
        return False


def check_and_init_sensors(use_esp32_indoor: bool = True) -> bool:
    """
    Check if sensors are connected and initialize them.
    Returns True if required sensors are OK, False otherwise.
    Also stores sensor status in Redis for dashboard display.

    If use_esp32_indoor is True, checks for ESP32 indoor data first,
    falls back to local DHT22 if not available.
    """
    global dht22_in, dht22_out

    ok = True
    sensor_status = {
        "indoor": {"ok": False, "error": None, "source": None},
        "outdoor": {"ok": False, "error": None},
    }

    # Check ESP32 indoor sensor first
    if use_esp32_indoor and check_esp32_indoor_available():
        print("✅ ESP32 indoor sensor data available")
        sensor_status["indoor"]["ok"] = True
        sensor_status["indoor"]["source"] = "esp32"
    else:
        # Try local DHT22 indoor sensor
        if dht22_in is None or dht22_out is None:
            init_dht22_sensors()

        if dht22_in is not None:
            try:
                _ = dht22_in.temperature
                _ = dht22_in.humidity
                print(f"✅ DHT22 indoor OK on GPIO {DHT22_INDOOR_PIN}")
                sensor_status["indoor"]["ok"] = True
                sensor_status["indoor"]["source"] = "dht22_local"
            except RuntimeError as e:
                print(f"⚠️ DHT22 indoor first read failed (normal): {e}")
                sensor_status["indoor"]["ok"] = True
                sensor_status["indoor"]["source"] = "dht22_local"
            except Exception as e:
                if use_esp32_indoor:
                    # If ESP32 mode is enabled but no data, still consider it ok (waiting for data)
                    sensor_status["indoor"]["ok"] = False
                    sensor_status["indoor"]["error"] = "Waiting for ESP32 data"
                    sensor_status["indoor"]["source"] = "esp32"
                    print("⏳ Waiting for ESP32 indoor sensor data...")
                else:
                    sensor_status["indoor"]["error"] = str(e)
                    ok = False
        else:
            if use_esp32_indoor:
                # ESP32 mode enabled, local DHT22 not required
                sensor_status["indoor"]["ok"] = False
                sensor_status["indoor"]["error"] = "Waiting for ESP32 data"
                sensor_status["indoor"]["source"] = "esp32"
                print("⏳ Waiting for ESP32 indoor sensor data...")
            else:
                sensor_status["indoor"]["error"] = "DHT22 indoor init failed"
                ok = False

    # Check outdoor sensor (always local DHT22)
    if dht22_in is None or dht22_out is None:
        init_dht22_sensors()

    if dht22_out is not None:
        try:
            _ = dht22_out.temperature
            _ = dht22_out.humidity
            print(f"✅ DHT22 outdoor OK on GPIO {DHT22_OUTDOOR_PIN}")
            sensor_status["outdoor"]["ok"] = True
        except RuntimeError as e:
            print(f"⚠️ DHT22 outdoor first read failed (normal): {e}")
            sensor_status["outdoor"]["ok"] = True
        except Exception as e:
            sensor_status["outdoor"]["error"] = str(e)
            ok = False
    else:
        sensor_status["outdoor"]["error"] = "DHT22 outdoor init failed"
        ok = False

    try:
        redis_client.set("sensor_status", json.dumps(sensor_status))
    except Exception as e:
        print(f"⚠️ No se pudo guardar estado de sensores en Redis: {e}")

    return ok


humidity_control_up = None
humidity_control_down = None


def setup_gpio() -> None:
    global humidity_control_up, humidity_control_down
    humidity_control_up = gpiozero.OutputDevice(HUMIDITY_CONTROL_PIN_UP, active_high=False, initial_value=False)
    humidity_control_down = gpiozero.OutputDevice(HUMIDITY_CONTROL_PIN_DOWN, active_high=False, initial_value=False)


def humidity_up_on() -> None:
    try:
        humidity_control_up.on()
        redis_client.set("humidity_control_up", "true")
        store_control_event("humidity_up", "on")
    except Exception as e:
        print(e)


def humidity_up_off() -> None:
    try:
        humidity_control_up.off()
        redis_client.set("humidity_control_up", "false")
        store_control_event("humidity_up", "off")
    except Exception as e:
        print(e)


def humidity_down_on() -> None:
    try:
        humidity_control_down.on()
        redis_client.set("humidity_control_down", "true")
        store_control_event("humidity_down", "on")
    except Exception as e:
        print(e)


def humidity_down_off() -> None:
    try:
        humidity_control_down.off()
        redis_client.set("humidity_control_down", "false")
        store_control_event("humidity_down", "off")
    except Exception as e:
        print(e)


def read_dht22(sensor, sensor_name: str, max_attempts: int = 5):
    """
    Read a DHT22 sensor with retry logic.
    Returns tuple (temperature, humidity) or (None, None) on failure.
    """
    if sensor is None:
        return None, None

    for attempt in range(max_attempts):
        try:
            temperature = sensor.temperature
            humidity = sensor.humidity
            if temperature is not None and humidity is not None:
                return temperature, humidity
        except RuntimeError:
            if attempt < max_attempts - 1:
                sleep(2)
            continue
        except Exception as e:
            print(f"⚠️ DHT22 {sensor_name} error: {e}")
            if attempt < max_attempts - 1:
                sleep(2)
            continue

    return None, None


def read_indoor_from_esp32(max_age_seconds: int = 60) -> tuple[float | None, float | None]:
    """
    Read indoor sensor data from ESP32 via Redis.
    Returns tuple (temperature, humidity) or (None, None) if data is stale or unavailable.
    """
    try:
        data = redis_client.get("esp32_indoor")
        if data is None:
            return None, None

        sensor_data = json.loads(data)
        timestamp = sensor_data.get("timestamp", 0)
        current_time = int(datetime.now(pytz.timezone("America/Argentina/Buenos_Aires")).timestamp())
        age = current_time - timestamp

        if age > max_age_seconds:
            print(f"⚠️ ESP32 indoor data is stale ({age}s old, max {max_age_seconds}s)")
            return None, None

        temperature = sensor_data.get("temperature")
        humidity = sensor_data.get("humidity")

        if temperature is not None and humidity is not None:
            return float(temperature), float(humidity)

        return None, None
    except Exception as e:
        print(f"⚠️ Error reading ESP32 indoor data: {e}")
        return None, None


def read_sensors(max_retries: int = 3, retry_delay: int = 2, use_esp32_indoor: bool = True):
    """
    Read both DHT22 sensors with retry logic.
    If use_esp32_indoor is True, reads indoor sensor from ESP32 data in Redis.
    """
    global dht22_in, dht22_out

    for attempt in range(max_retries):
        json_data = {}

        # Try to read indoor sensor from ESP32 first
        if use_esp32_indoor:
            temperature_c, humidity = read_indoor_from_esp32(max_age_seconds=60)
            if temperature_c is not None and humidity is not None:
                print(f"📡 Using ESP32 indoor sensor: {temperature_c:.1f}°C, {humidity:.1f}%")
                json_data["temperature"] = round(temperature_c, 2)
                json_data["humidity"] = round(humidity, 2)
                json_data["vpd"] = calculate_vpd(temperature_c, humidity)
                json_data["indoor_source"] = "esp32"
            else:
                # ESP32 data not available or stale, try local DHT22
                print(f"⚠️ ESP32 indoor data unavailable, trying local DHT22 (attempt {attempt + 1}/{max_retries})")
                if dht22_in is None:
                    check_and_init_sensors()
                    sleep(retry_delay)
                    continue

                temperature_c, humidity = read_dht22(dht22_in, "indoor")
                if temperature_c is None or humidity is None:
                    if attempt < max_retries - 1:
                        sleep(retry_delay)
                    continue

                json_data["temperature"] = round(temperature_c, 2)
                json_data["humidity"] = round(humidity, 2)
                json_data["vpd"] = calculate_vpd(temperature_c, humidity)
                json_data["indoor_source"] = "dht22_local"
        else:
            # Original behavior: read from local DHT22
            if dht22_in is None:
                print(f"⚠️ Indoor sensor not initialized, attempting reinit (attempt {attempt + 1}/{max_retries})")
                check_and_init_sensors()
                sleep(retry_delay)
                continue

            temperature_c, humidity = read_dht22(dht22_in, "indoor")
            if temperature_c is None or humidity is None:
                print(f"⚠️ Indoor sensor read failed (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    sleep(retry_delay)
                continue

            json_data["temperature"] = round(temperature_c, 2)
            json_data["humidity"] = round(humidity, 2)
            json_data["vpd"] = calculate_vpd(temperature_c, humidity)
            json_data["indoor_source"] = "dht22_local"

        # If we don't have indoor data yet, continue retrying
        if "temperature" not in json_data:
            if attempt < max_retries - 1:
                sleep(retry_delay)
            continue

        # Read outdoor sensor (always from local DHT22)
        outside_temperature_c, outside_humidity = read_dht22(dht22_out, "outdoor")
        if outside_temperature_c is not None and outside_humidity is not None:
            json_data["outside_temperature"] = round(outside_temperature_c, 2)
            json_data["outside_humidity"] = round(outside_humidity, 2)
        else:
            print("⚠️ Outdoor sensor read failed, using fallback values")
            json_data["outside_temperature"] = json_data["temperature"]
            json_data["outside_humidity"] = json_data["humidity"]

        return json_data

    print("❌ Failed to read sensors after maximum retries")
    return None


def store_historical_data(sensors_data: dict) -> None:
    """
    Store historical temperature and humidity data in Redis with different time windows.
    """
    argentina_tz = pytz.timezone("America/Argentina/Buenos_Aires")
    current_time = datetime.now(argentina_tz)
    current_timestamp = int(current_time.timestamp())

    data_point = {
        "timestamp": current_timestamp,
        "datetime": current_time.strftime("%Y-%m-%d %H:%M:%S"),
        "temperature": sensors_data["temperature"],
        "humidity": sensors_data["humidity"],
    }

    time_windows = {
        "6h": {"duration": 6 * 3600, "interval": 3600},
        "12h": {"duration": 12 * 3600, "interval": 6 * 3600},
        "24h": {"duration": 24 * 3600, "interval": 12 * 3600},
        "1w": {"duration": 7 * 24 * 3600, "interval": 24 * 3600},
    }

    for window, config in time_windows.items():
        key = f"historical_data_{window}"
        buffer_key = f"historical_buffer_{window}"

        existing_data = redis_client.get(key)
        data_list = json.loads(existing_data) if existing_data else []

        buffer_data = redis_client.get(buffer_key)
        buffer_list = json.loads(buffer_data) if buffer_data else []

        buffer_list.append(data_point)

        if buffer_list and (len(buffer_list) == 1 or current_timestamp - buffer_list[0]["timestamp"] >= config["interval"]):
            avg_temperature = sum(point["temperature"] for point in buffer_list) / len(buffer_list)
            avg_humidity = sum(point["humidity"] for point in buffer_list) / len(buffer_list)
            avg_point = {
                "timestamp": current_timestamp,
                "datetime": current_time.strftime("%Y-%m-%d %H:%M:%S"),
                "temperature": round(avg_temperature, 2),
                "humidity": round(avg_humidity, 2),
            }
            data_list.append(avg_point)
            buffer_list = []

        cutoff_time = current_timestamp - config["duration"]
        data_list = [point for point in data_list if point["timestamp"] > cutoff_time]

        redis_client.set(key, json.dumps(data_list))
        redis_client.set(buffer_key, json.dumps(buffer_list))


def all_outputs_off() -> None:
    """Turn off all outputs safely."""
    try:
        if humidity_control_up:
            humidity_control_up.off()
        if humidity_control_down:
            humidity_control_down.off()
        redis_client.set("humidity_control_up", "false")
        redis_client.set("humidity_control_down", "false")
    except Exception as e:
        print(f"⚠️ Error turning off outputs: {e}")


def main(stage_override: str | None = None, use_esp32_indoor: bool = True) -> None:
    setup_gpio()
    all_outputs_off()

    if use_esp32_indoor:
        print("📡 Modo ESP32: usando sensor indoor vía HTTP/Redis")
    else:
        print("🔌 Modo local: usando sensor indoor DHT22 en GPIO")

    while not check_and_init_sensors(use_esp32_indoor=use_esp32_indoor):
        print("🔄 Sensores no detectados - reintentando en 5 segundos...")
        sleep(5)

    current_stage = None
    current_grow_id = None
    stage_check_counter = 0
    STAGE_CHECK_INTERVAL = 20

    last_db_save_time = 0
    DB_SAVE_INTERVAL = 300

    humidity_control_mode = None  # None, 'raising', or 'lowering'

    while True:
        try:
            if stage_check_counter == 0:
                active_grow = get_active_grow()
                if not active_grow:
                    print("No active grow found. Please create a grow first.")
                    sleep(5)
                    continue

                if stage_override and stage_override in ["early_veg", "late_veg", "flowering", "dry"]:
                    new_stage = stage_override
                    stage_source = "override"
                else:
                    new_stage = active_grow["stage"]
                    stage_source = f"grow '{active_grow['name']}'"

                if new_stage != current_stage or active_grow["id"] != current_grow_id:
                    if current_stage is not None:
                        print(f"\n🔄 Stage changed: {current_stage} → {new_stage}")
                        print(f"   Source: {stage_source}")
                    else:
                        print(f"\n✅ Starting with stage: {new_stage}")
                        print(f"   Source: {stage_source}")

                    current_stage = new_stage
                    current_grow_id = active_grow["id"]
                    humidity_control_mode = None

                STAGE = current_stage

            stage_check_counter = (stage_check_counter + 1) % STAGE_CHECK_INTERVAL

            sensors_data = read_sensors(use_esp32_indoor=use_esp32_indoor)
            if sensors_data is None:
                print("⚠️ No valid sensor data - attempting sensor reinit...")
                all_outputs_off()
                humidity_control_mode = None
                check_and_init_sensors(use_esp32_indoor=use_esp32_indoor)
                sleep(3)
                continue

            temperature = float(sensors_data["temperature"])
            humidity = float(sensors_data["humidity"])
            leaf_temperature = round(temperature - 1.5, 1)
            leaf_vpd = calculate_vpd(leaf_temperature, humidity)
            humidity_is_in_range = False

            store_historical_data(sensors_data)

            sensors_data["leaf_temperature"] = leaf_temperature
            sensors_data["leaf_vpd"] = leaf_vpd

            if STAGE != "dry":
                target_humidity = calculate_target_humidity(STAGE, temperature)
                sensors_data["target_humidity"] = target_humidity
                sensors_data["vpd_in_range"] = vpd_is_in_range(leaf_vpd, STAGE)
            else:
                sensors_data["vpd_in_range"] = False
                if 60 <= humidity <= 65:
                    target_humidity = humidity
                    humidity_is_in_range = True
                    humidity_control_mode = None
                elif humidity >= 65:
                    target_humidity = 60
                else:
                    target_humidity = 65
                sensors_data["target_humidity"] = target_humidity

            redis_client.set("sensors", json.dumps(sensors_data))

            current_time = datetime.now().timestamp()
            if current_time - last_db_save_time >= DB_SAVE_INTERVAL:
                store_sensor_sample(sensors_data)
                last_db_save_time = current_time
                print("💾 Sample saved to database (next save in 5 minutes)")

            if STAGE != "dry":
                if humidity_control_mode == "raising":
                    if humidity >= target_humidity:
                        print(f"✅ Target humidity reached ({humidity:.1f}% >= {target_humidity}%), stopping humidifier")
                        humidity_up_off()
                        humidity_down_off()
                        humidity_control_mode = None
                        sleep(3)
                        continue
                elif humidity_control_mode == "lowering":
                    if humidity <= target_humidity:
                        print(f"✅ Target humidity reached ({humidity:.1f}% <= {target_humidity}%), stopping dehumidifier")
                        humidity_up_off()
                        humidity_down_off()
                        humidity_control_mode = None
                        sleep(3)
                        continue
                elif sensors_data["vpd_in_range"]:
                    humidity_up_off()
                    humidity_down_off()
                    sleep(3)
                    continue

            if humidity_is_in_range:
                humidity_up_off()
                humidity_down_off()
                humidity_control_mode = None
                sleep(3)
                continue

            if target_humidity is None:
                continue

            if humidity < target_humidity:
                if humidity_control_mode != "raising":
                    print(f"🔼 Starting to raise humidity ({humidity:.1f}% → {target_humidity}%)")
                    humidity_control_mode = "raising"
                humidity_up_on()
                humidity_down_off()
            elif humidity > target_humidity:
                if humidity_control_mode != "lowering":
                    print(f"🔽 Starting to lower humidity ({humidity:.1f}% → {target_humidity}%)")
                    humidity_control_mode = "lowering"
                humidity_up_off()
                humidity_down_on()
            else:
                humidity_control_mode = None
                humidity_up_off()
                humidity_down_off()

            sleep(3)

        except Exception as e:
            print(f"❌ Error in main loop: {e}")
            sleep(3)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="VPD control loop")
    parser.add_argument(
        "stage",
        nargs="?",
        choices=["early_veg", "late_veg", "flowering", "dry"],
        help="Override stage (optional)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local DHT22 sensor for indoor readings instead of ESP32",
    )
    parser.add_argument(
        "--esp32",
        action="store_true",
        default=True,
        help="Use ESP32 sensor for indoor readings (default)",
    )

    args = parser.parse_args()

    # --local flag disables ESP32 mode
    use_esp32 = not args.local

    main(stage_override=args.stage, use_esp32_indoor=use_esp32)

