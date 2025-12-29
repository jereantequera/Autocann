#!/usr/bin/env python

import json
import math
import sys
from datetime import datetime
from time import sleep

import adafruit_dht
import board
import gpiozero
import pytz
import redis
from database import get_active_grow, store_control_event, store_sensor_sample

HUMIDITY_CONTROL_PIN_UP = 25
HUMIDITY_CONTROL_PIN_DOWN = 16
VENTILATION_CONTROL_PIN = 7

# DHT22 sensor GPIO pins
DHT22_INDOOR_PIN = 4
DHT22_OUTDOOR_PIN = 13

EARLY_VEG_VPD_RANGE = (0.6, 1.0)
LATE_VEG_VPD_RANGE = (0.8, 1.2)
FLOWERING_VPD_RANGE = (1.2, 1.5)

redis_client = redis.Redis(host='localhost', port=6379, db=0)

# Sensor globals (initialized by check_and_init_sensors)
dht22_in = None
dht22_out = None


def get_board_pin(gpio_num):
    """
    Map GPIO number to board pin object.
    """
    gpio_to_board = {
        4: board.D4,
        13: board.D13,
    }
    return gpio_to_board.get(gpio_num)


def init_dht22_sensors():
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


def check_and_init_sensors():
    """
    Check if DHT22 sensors are connected and initialize them.
    - DHT22 indoor on GPIO 4
    - DHT22 outdoor on GPIO 13
    Returns True if both sensors are OK, False otherwise.
    Also stores sensor status in Redis for dashboard display.
    """
    global dht22_in, dht22_out

    ok = True
    sensor_status = {
        "indoor": {"ok": False, "error": None},
        "outdoor": {"ok": False, "error": None},
    }

    # Initialize sensors if not already done
    if dht22_in is None or dht22_out is None:
        init_dht22_sensors()

    # Test indoor sensor
    if dht22_in is not None:
        try:
            _ = dht22_in.temperature
            _ = dht22_in.humidity
            print(f"✅ DHT22 indoor OK on GPIO {DHT22_INDOOR_PIN}")
            sensor_status["indoor"]["ok"] = True
        except RuntimeError as e:
            # DHT sensors often fail first read, that's normal
            print(f"⚠️ DHT22 indoor first read failed (normal): {e}")
            sensor_status["indoor"]["ok"] = True  # Still mark as OK
        except Exception as e:
            error_msg = str(e)
            print(f"❌ DHT22 indoor error: {e}")
            sensor_status["indoor"]["error"] = error_msg
            ok = False
    else:
        sensor_status["indoor"]["error"] = "DHT22 indoor init failed"
        ok = False

    # Test outdoor sensor
    if dht22_out is not None:
        try:
            _ = dht22_out.temperature
            _ = dht22_out.humidity
            print(f"✅ DHT22 outdoor OK on GPIO {DHT22_OUTDOOR_PIN}")
            sensor_status["outdoor"]["ok"] = True
        except RuntimeError as e:
            # DHT sensors often fail first read, that's normal
            print(f"⚠️ DHT22 outdoor first read failed (normal): {e}")
            sensor_status["outdoor"]["ok"] = True  # Still mark as OK
        except Exception as e:
            error_msg = str(e)
            print(f"❌ DHT22 outdoor error: {e}")
            sensor_status["outdoor"]["error"] = error_msg
            ok = False
    else:
        sensor_status["outdoor"]["error"] = "DHT22 outdoor init failed"
        ok = False

    # Store sensor status in Redis for dashboard
    try:
        redis_client.set("sensor_status", json.dumps(sensor_status))
    except Exception as e:
        print(f"⚠️ No se pudo guardar estado de sensores en Redis: {e}")

    return ok


humidity_control_up = None
humidity_control_down = None
ventilation_control = None


def setup_gpio():
    global humidity_control_up
    global humidity_control_down
    global ventilation_control
    try:
        # active_high=False because relay modules are typically active-low
        # (relay activates when pin is LOW, deactivates when pin is HIGH)
        humidity_control_up = gpiozero.OutputDevice(HUMIDITY_CONTROL_PIN_UP, active_high=False, initial_value=False)
        humidity_control_down = gpiozero.OutputDevice(HUMIDITY_CONTROL_PIN_DOWN, active_high=False, initial_value=False)
        ventilation_control = gpiozero.OutputDevice(VENTILATION_CONTROL_PIN, active_high=False, initial_value=False)
    except Exception as e:
        raise e


def humidity_up_on():
    try:
        humidity_control_up.on()
        redis_client.set('humidity_control_up', 'true')
        store_control_event('humidity_up', 'on')
    except Exception as e:
        print(e)

def humidity_up_off():
    try:
        humidity_control_up.off()
        redis_client.set('humidity_control_up', 'false')
        store_control_event('humidity_up', 'off')
    except Exception as e:
        print(e)

def humidity_down_on():
    try:
        humidity_control_down.on()
        redis_client.set('humidity_control_down', 'true')
        store_control_event('humidity_down', 'on')
    except Exception as e:
        print(e)

def humidity_down_off():
    try:
        humidity_control_down.off()
        redis_client.set('humidity_control_down', 'false')
        store_control_event('humidity_down', 'off')
    except Exception as e:
        print(e)

def ventilation_on():
    try:
        ventilation_control.on()
        redis_client.set('ventilation_control', 'true')
        store_control_event('ventilation', 'on')
    except Exception as e:
        print(e)

def ventilation_off():
    try:
        ventilation_control.off()
        redis_client.set('ventilation_control', 'false')
        store_control_event('ventilation', 'off')
    except Exception as e:
        print(e)

def calculate_humidity_for_vpd(temperature, target_vpd):
    """
    Calculate the relative humidity needed to achieve a target VPD at a given temperature.
    
    Parameters:
    - temperature: Temperature in Celsius degrees.
    - target_vpd: The desired VPD in kPa.
    
    Returns:
    - humidity: The required relative humidity in percentage.
    """
    # Calculate the saturation vapor pressure (SVP)
    svp = 0.6108 * math.exp((17.27 * temperature) / (temperature + 237.3))
    
    # Calculate the required relative humidity
    avp = svp - target_vpd
    humidity = (avp / svp) * 100
    
    # Ensure the humidity is within a valid range
    if humidity < 0:
        humidity = 0
    elif humidity > 100:
        humidity = 100
    
    return round(humidity, 2)

def calculate_vpd(temperature, humidity):
    """
    Calculates the Vapor Pressure Deficit (VPD).
    
    Parameters:
    - temperature: Temperature in Celsius degrees.
    - humidity: Relative humidity in percentage (0-100).
    
    Returns:
    - vpd: Vapor Pressure Deficit in kPa.
    """
    # Calculate the saturation vapor pressure (SVP)
    svp = 0.6108 * math.exp((17.27 * temperature) / (temperature + 237.3))
    
    # Calculate the actual vapor pressure (AVP)
    avp = svp * (humidity / 100.0)
    
    # Calculate the VPD
    vpd = svp - avp
    return round(vpd, 2)

def calculate_humidity_range_boudns(stage, temperature):
    if stage == "early_veg":
        low_bound = calculate_humidity_for_vpd(temperature, EARLY_VEG_VPD_RANGE[0])
        high_bound = calculate_humidity_for_vpd(temperature, EARLY_VEG_VPD_RANGE[1])
        return low_bound, high_bound
    elif stage == "late_veg":
        low_bound = calculate_humidity_for_vpd(temperature, LATE_VEG_VPD_RANGE[0])
        high_bound = calculate_humidity_for_vpd(temperature, LATE_VEG_VPD_RANGE[1])
        return low_bound, high_bound
    elif stage == "flowering":
        low_bound = calculate_humidity_for_vpd(temperature, FLOWERING_VPD_RANGE[0])
        high_bound = calculate_humidity_for_vpd(temperature, FLOWERING_VPD_RANGE[1])
        return low_bound, high_bound

def calculate_target_humidity(stage, temperature):
    low_bound, high_bound = calculate_humidity_range_boudns(stage, temperature)
    return round((low_bound + high_bound) / 2, 0)

def vpd_is_in_range(vpd, stage):
    if stage == "early_veg":
        if vpd < EARLY_VEG_VPD_RANGE[0] or vpd > EARLY_VEG_VPD_RANGE[1]:
            return False
    elif stage == "late_veg":
        if vpd < LATE_VEG_VPD_RANGE[0] or vpd > LATE_VEG_VPD_RANGE[1]:
            return False
    elif stage == "flowering":
        if vpd < FLOWERING_VPD_RANGE[0] or vpd > FLOWERING_VPD_RANGE[1]:
            return False
    return True


def read_dht22(sensor, sensor_name, max_attempts=5):
    """
    Read a DHT22 sensor with retry logic.
    DHT sensors are notorious for failing reads, so we retry multiple times.
    
    Parameters:
    - sensor: The DHT22 sensor object
    - sensor_name: Name for logging purposes
    - max_attempts: Maximum number of retry attempts
    
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
                
        except RuntimeError as e:
            # DHT sensors often throw RuntimeError on failed reads
            if attempt < max_attempts - 1:
                sleep(2)  # DHT22 needs at least 2 seconds between reads
            continue
        except Exception as e:
            print(f"⚠️ DHT22 {sensor_name} error: {e}")
            if attempt < max_attempts - 1:
                sleep(2)
            continue
    
    return None, None


def read_sensors(max_retries=3, retry_delay=2):
    """
    Read both DHT22 sensors with retry logic.
    - DHT22 indoor on GPIO 4
    - DHT22 outdoor on GPIO 13
    
    Parameters:
    - max_retries: Maximum number of retry attempts
    - retry_delay: Delay between retries in seconds
    """
    global dht22_in, dht22_out
    
    for attempt in range(max_retries):
        json_data = {}
        
        # Check if sensors are initialized
        if dht22_in is None:
            print(f"⚠️ Indoor sensor not initialized, attempting reinit (attempt {attempt + 1}/{max_retries})")
            if check_and_init_sensors():
                sleep(retry_delay)
                continue
            else:
                sleep(retry_delay)
                continue
        
        # Read indoor sensor
        temperature_c, humidity = read_dht22(dht22_in, "indoor")
        
        if temperature_c is None or humidity is None:
            print(f"⚠️ Indoor sensor read failed (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                sleep(retry_delay)
            continue
        
        json_data['temperature'] = round(temperature_c, 2)
        json_data['humidity'] = round(humidity, 2)
        json_data['vpd'] = calculate_vpd(temperature_c, humidity)
        
        # Read outdoor sensor
        outside_temperature_c, outside_humidity = read_dht22(dht22_out, "outdoor")
        
        if outside_temperature_c is not None and outside_humidity is not None:
            json_data['outside_temperature'] = round(outside_temperature_c, 2)
            json_data['outside_humidity'] = round(outside_humidity, 2)
        else:
            # Use fallback values if outdoor DHT22 fails (use indoor values as approximation)
            print(f"⚠️ Outdoor sensor read failed, using fallback values")
            json_data['outside_temperature'] = json_data['temperature']
            json_data['outside_humidity'] = json_data['humidity']
        
        return json_data
    
    print("❌ Failed to read sensors after maximum retries")
    return None


def store_historical_data(sensors_data):
    """
    Store historical temperature and humidity data in Redis with different time windows.
    Each data point includes timestamp, temperature and humidity.
    Data is stored at different intervals:
    - 6h: every hour
    - 12h: every 6 hours (average)
    - 24h: every 12 hours (average)
    - 1w: every 24 hours (average)
    """
    # Get current time in Argentina timezone
    argentina_tz = pytz.timezone('America/Argentina/Buenos_Aires')
    current_time = datetime.now(argentina_tz)
    current_timestamp = int(current_time.timestamp())
    
    data_point = {
        'timestamp': current_timestamp,
        'datetime': current_time.strftime('%Y-%m-%d %H:%M:%S'),
        'temperature': sensors_data['temperature'],
        'humidity': sensors_data['humidity']
    }
    
    # Define time windows and their intervals
    time_windows = {
        '6h': {'duration': 6 * 3600, 'interval': 3600},  # 6 hours, store every hour
        '12h': {'duration': 12 * 3600, 'interval': 6 * 3600},  # 12 hours, store every 6 hours
        '24h': {'duration': 24 * 3600, 'interval': 12 * 3600},  # 24 hours, store every 12 hours
        '1w': {'duration': 7 * 24 * 3600, 'interval': 24 * 3600}  # 1 week, store every 24 hours
    }
    
    # Store data for each time window
    for window, config in time_windows.items():
        key = f'historical_data_{window}'
        buffer_key = f'historical_buffer_{window}'
        
        # Get existing data
        existing_data = redis_client.get(key)
        if existing_data:
            data_list = json.loads(existing_data)
        else:
            data_list = []
            
        # Get or create buffer for averaging
        buffer_data = redis_client.get(buffer_key)
        if buffer_data:
            buffer_list = json.loads(buffer_data)
        else:
            buffer_list = []
            
        # Add new data point to buffer
        buffer_list.append(data_point)
        
        # Check if we need to create a new average point
        if buffer_list and (len(buffer_list) == 1 or 
            current_timestamp - buffer_list[0]['timestamp'] >= config['interval']):
            
            # Calculate average
            avg_temperature = sum(point['temperature'] for point in buffer_list) / len(buffer_list)
            avg_humidity = sum(point['humidity'] for point in buffer_list) / len(buffer_list)
            
            # Create new average point
            avg_point = {
                'timestamp': current_timestamp,
                'datetime': current_time.strftime('%Y-%m-%d %H:%M:%S'),
                'temperature': round(avg_temperature, 2),
                'humidity': round(avg_humidity, 2)
            }
            
            # Add to data list
            data_list.append(avg_point)
            
            # Clear buffer
            buffer_list = []
        
        # Remove old data points
        cutoff_time = current_timestamp - config['duration']
        data_list = [point for point in data_list if point['timestamp'] > cutoff_time]
        
        # Store updated data and buffer
        redis_client.set(key, json.dumps(data_list))
        redis_client.set(buffer_key, json.dumps(buffer_list))

def all_outputs_off():
    """Turn off all outputs safely."""
    try:
        if humidity_control_up:
            humidity_control_up.off()
        if humidity_control_down:
            humidity_control_down.off()
        if ventilation_control:
            ventilation_control.off()
        # Update Redis state
        redis_client.set('humidity_control_up', 'false')
        redis_client.set('humidity_control_down', 'false')
        redis_client.set('ventilation_control', 'false')
    except Exception as e:
        print(f"⚠️ Error turning off outputs: {e}")


def main(stage_override=None):
    # Setup GPIO first
    setup_gpio()
    
    # Ensure all outputs are off at startup
    all_outputs_off()

    # Initialize DHT22 sensors
    while not check_and_init_sensors():
        print("🔄 Sensores no detectados - reintentando en 5 segundos...")
        sleep(5)
    
    # Keep track of current stage to detect changes
    current_stage = None
    current_grow_id = None
    stage_check_counter = 0
    STAGE_CHECK_INTERVAL = 20  # Check for stage changes every 20 iterations (~60 seconds)
    
    # Control for SQLite storage (every 5 minutes minimum)
    last_db_save_time = 0
    DB_SAVE_INTERVAL = 300  # 5 minutes in seconds
    
    # Hysteresis control: track if we're actively raising or lowering humidity
    # When raising, continue until target is reached (not just until VPD is in range)
    # This prevents constant cycling and keeps average humidity higher
    humidity_control_mode = None  # None, 'raising', or 'lowering'
    
    while True:
        try:
            # Check for stage changes periodically
            if stage_check_counter == 0:
                active_grow = get_active_grow()
                if not active_grow:
                    print("No active grow found. Please create a grow first.")
                    sleep(5)
                    continue
                
                # Use stage from active grow, or override from command line
                if stage_override and stage_override in ["early_veg", "late_veg", "flowering", "dry"]:
                    new_stage = stage_override
                    stage_source = "override"
                else:
                    new_stage = active_grow['stage']
                    stage_source = f"grow '{active_grow['name']}'"
                
                # Detect stage or grow changes
                if new_stage != current_stage or active_grow['id'] != current_grow_id:
                    if current_stage is not None:
                        print(f"\n🔄 Stage changed: {current_stage} → {new_stage}")
                        print(f"   Source: {stage_source}")
                    else:
                        print(f"\n✅ Starting with stage: {new_stage}")
                        print(f"   Source: {stage_source}")
                    
                    current_stage = new_stage
                    current_grow_id = active_grow['id']
                    # Reset humidity control mode on stage change
                    humidity_control_mode = None
                
                STAGE = current_stage
            
            # Increment counter and reset when reaching interval
            stage_check_counter = (stage_check_counter + 1) % STAGE_CHECK_INTERVAL
            
            sensors_data = read_sensors()
            if sensors_data is None:
                # Try to reinitialize sensors
                print("⚠️ No valid sensor data - attempting sensor reinit...")
                all_outputs_off()  # Safety: turn off all outputs
                humidity_control_mode = None  # Reset control mode on error
                check_and_init_sensors()
                sleep(3)
                continue
            
            temperature = float(sensors_data['temperature'])
            humidity = float(sensors_data['humidity'])
            outside_humidity = float(sensors_data['outside_humidity'])
            leaf_temperature = round(temperature - 1.5, 1)
            leaf_vpd = calculate_vpd(leaf_temperature, humidity)
            humidity_is_in_range = False
            # Store historical data in Redis
            store_historical_data(sensors_data)            
            
            # Always calculate leaf data
            sensors_data['leaf_temperature'] = leaf_temperature
            sensors_data['leaf_vpd'] = leaf_vpd
            
            if STAGE != "dry":
                target_humidity = calculate_target_humidity(STAGE, temperature)
                sensors_data['target_humidity'] = target_humidity
                sensors_data['vpd_in_range'] = vpd_is_in_range(leaf_vpd, STAGE)
                
                # Hysteresis logic: when actively raising/lowering, continue until target is reached
                # This prevents the humidifier from cycling on/off rapidly
                if humidity_control_mode == 'raising':
                    if humidity >= target_humidity:
                        # Reached target humidity - stop raising
                        print(f"✅ Target humidity reached ({humidity:.1f}% >= {target_humidity}%), stopping humidifier")
                        humidity_up_off()
                        humidity_down_off()
                        ventilation_off()
                        humidity_control_mode = None
                        redis_client.set('sensors', json.dumps(sensors_data))
                        sleep(3)
                        continue
                    # Still below target - continue raising (don't stop just because VPD is in range)
                    # The control logic below will keep the humidifier running
                    
                elif humidity_control_mode == 'lowering':
                    if humidity <= target_humidity:
                        # Reached target humidity - stop lowering
                        print(f"✅ Target humidity reached ({humidity:.1f}% <= {target_humidity}%), stopping dehumidifier")
                        humidity_up_off()
                        humidity_down_off()
                        ventilation_off()
                        humidity_control_mode = None
                        redis_client.set('sensors', json.dumps(sensors_data))
                        sleep(3)
                        continue
                    # Still above target - continue lowering
                    
                elif sensors_data['vpd_in_range']:
                    # Not actively controlling and VPD is in range - do nothing
                    humidity_up_off()
                    humidity_down_off()
                    ventilation_off()
                    redis_client.set('sensors', json.dumps(sensors_data))
                    sleep(3)
                    continue
            else:
                # Dry mode: target is 60-65% humidity
                sensors_data['vpd_in_range'] = False
                if humidity >= 60 and humidity <= 65:
                    target_humidity = humidity
                    humidity_is_in_range = True
                    humidity_control_mode = None  # Reset mode when in range
                elif humidity >= 65:
                    target_humidity = 60
                elif humidity <= 60:
                    target_humidity = 65
                sensors_data['target_humidity'] = target_humidity
            
            # Store in Redis for real-time access
            redis_client.set('sensors', json.dumps(sensors_data))
            
            # Store in SQLite for historical persistence (every 5 minutes minimum)
            current_time = datetime.now().timestamp()
            if current_time - last_db_save_time >= DB_SAVE_INTERVAL:
                store_sensor_sample(sensors_data)
                last_db_save_time = current_time
                print(f"💾 Sample saved to database (next save in 5 minutes)")
            
            if humidity_is_in_range:
                # Humidity is in dry range - turn off all controls
                humidity_up_off()
                humidity_down_off()
                ventilation_off()
                humidity_control_mode = None
                sleep(3)
                continue

            if target_humidity is None:
                continue
            if humidity < target_humidity:
                # Start or continue raising humidity
                if humidity_control_mode != 'raising':
                    print(f"🔼 Starting to raise humidity ({humidity:.1f}% → {target_humidity}%)")
                    humidity_control_mode = 'raising'
                humidity_up_on()
                humidity_down_off()
                if outside_humidity > target_humidity:
                    ventilation_on()
                else:
                    ventilation_off()
            elif humidity > target_humidity:
                # Start or continue lowering humidity
                if humidity_control_mode != 'lowering':
                    print(f"🔽 Starting to lower humidity ({humidity:.1f}% → {target_humidity}%)")
                    humidity_control_mode = 'lowering'
                humidity_up_off()
                humidity_down_on()
                if outside_humidity < target_humidity:
                    ventilation_on()
                else:
                    ventilation_off()
            elif humidity == target_humidity:
                # At target - stop all controls
                humidity_control_mode = None
                humidity_up_off()
                humidity_down_off()
                ventilation_off()
            sleep(3)
            
        except Exception as e:
            print(f"❌ Error in main loop: {e}")
            sleep(3)


if __name__ == "__main__":
    # Optional stage override from command line
    # If not provided, will use the stage from the active grow in the database
    stage_override = sys.argv[1] if len(sys.argv) > 1 else None
    main(stage_override)
