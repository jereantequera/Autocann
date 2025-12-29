#!/usr/bin/env python

import json
import math
import sys
from datetime import datetime
from time import sleep

import adafruit_bme280.advanced as adafruit_bme280
import board
import busio
import gpiozero
import pytz
import redis
from database import get_active_grow, store_control_event, store_sensor_sample

HUMIDITY_CONTROL_PIN_UP = 25
HUMIDITY_CONTROL_PIN_DOWN = 16
VENTILATION_CONTROL_PIN = 7


EARLY_VEG_VPD_RANGE = (0.6, 1.0)
LATE_VEG_VPD_RANGE = (0.8, 1.2)
FLOWERING_VPD_RANGE = (1.2, 1.5)

redis_client = redis.Redis(host='localhost', port=6379, db=0)

# BME280 sensor addresses
BME280_ADDRESSES = [0x76, 0x77]

# I2C bus - will be initialized in init_i2c_bus()
i2c = None

# Sensor globals (initialized by check_and_init_sensors)
bme280_in = None
bme280_out = None



def init_i2c_bus():
    """
    Initialize or reinitialize the I2C bus.
    Returns the I2C bus object or None if failed.
    """
    global i2c
    
    try:
        # If there's an existing I2C bus, try to close it
        if i2c is not None:
            try:
                i2c.deinit()
            except Exception:
                pass
            i2c = None
        
        sleep(0.1)
        
        # Create new I2C bus
        i2c = busio.I2C(board.SCL, board.SDA)
        return i2c
        
    except Exception as e:
        print(f"❌ I2C init failed: {e}")
        return None


def i2c_clock_recovery():
    """
    Attempt to recover I2C bus by sending 9 clock pulses on SCL.
    Standard I2C recovery technique when a slave is holding SDA low.
    """
    try:
        global i2c
        if i2c is not None:
            try:
                i2c.deinit()
            except Exception:
                pass
            i2c = None
        
        # Use GPIO to manually toggle SCL 9 times (GPIO3 = SCL on RPi)
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(3, GPIO.OUT)
        
        for _ in range(9):
            GPIO.output(3, GPIO.HIGH)
            sleep(0.001)
            GPIO.output(3, GPIO.LOW)
            sleep(0.001)
        
        GPIO.output(3, GPIO.HIGH)
        GPIO.cleanup([3])
        
        return init_i2c_bus()
        
    except ImportError:
        return recover_i2c_via_kernel()
    except Exception:
        return recover_i2c_via_kernel()


def recover_i2c_via_kernel():
    """
    Try to recover I2C by reloading the kernel module.
    """
    import subprocess
    
    try:
        global i2c
        if i2c is not None:
            try:
                i2c.deinit()
            except Exception:
                pass
            i2c = None
        
        # Reload i2c-bcm2835 module (requires root)
        try:
            subprocess.run(['sudo', 'rmmod', 'i2c_bcm2835'], 
                         capture_output=True, timeout=3)
            subprocess.run(['sudo', 'modprobe', 'i2c_bcm2835'], 
                         capture_output=True, timeout=3)
            sleep(0.2)
        except Exception:
            pass
        
        return init_i2c_bus()
        
    except Exception:
        return init_i2c_bus()


def recover_i2c_bus():
    """
    Main I2C recovery function. Tries all methods immediately until one works.
    """
    print("🔄 I2C recovery...")
    
    # Method 1: Simple reinitialize
    if init_i2c_bus():
        try:
            while not i2c.try_lock():
                pass
            found = i2c.scan()
            i2c.unlock()
            if found:
                print(f"✅ Recovered (reinit): {[hex(a) for a in found]}")
                return True
        except Exception:
            pass
    
    # Method 2: Clock recovery (9 pulses)
    if i2c_clock_recovery():
        try:
            while not i2c.try_lock():
                pass
            found = i2c.scan()
            i2c.unlock()
            if found:
                print(f"✅ Recovered (clock): {[hex(a) for a in found]}")
                return True
        except Exception:
            pass
    
    # Method 3: Final reinit attempt
    if init_i2c_bus():
        try:
            while not i2c.try_lock():
                pass
            found = i2c.scan()
            i2c.unlock()
            if found:
                print(f"✅ Recovered (retry): {[hex(a) for a in found]}")
                return True
        except Exception:
            pass
    
    print("❌ Recovery failed - check wiring or reboot")
    return False


def check_and_init_sensors(try_recovery=True):
    """
    Check if BME280 sensors are connected and initialize them.
    Returns True if both sensors are OK, False otherwise.
    Also stores sensor status in Redis for dashboard display.
    
    Parameters:
    - try_recovery: If True and sensors not found, attempt I2C bus recovery
    """
    global bme280_in, bme280_out, i2c

    # Ensure I2C bus is initialized
    if i2c is None:
        if not init_i2c_bus():
            return False

    # Scan I2C bus
    found = []
    try:
        while not i2c.try_lock():
            pass
        found = i2c.scan()
        i2c.unlock()
    except Exception as e:
        print(f"❌ I2C scan failed: {e}")
        if try_recovery:
            if recover_i2c_bus():
                return check_and_init_sensors(try_recovery=False)
        return False

    # If no devices found, try recovery
    if not found:
        print("⚠️ No I2C devices detected on bus")
        if try_recovery:
            if recover_i2c_bus():
                return check_and_init_sensors(try_recovery=False)
        return False

    ok = True
    sensor_status = {
        "indoor": {"ok": False, "error": None},
        "outdoor": {"ok": False, "error": None},
    }

    for addr in BME280_ADDRESSES:
        sensor_name = "indoor" if addr == 0x76 else "outdoor"

        if addr not in found:
            error_msg = "Sensor no detectado"
            print(f"❌ BME280 {sensor_name} no detectado en 0x{addr:02x}")
            sensor_status[sensor_name]["error"] = error_msg
            ok = False
            continue

        try:
            bme = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=addr)
            # Test read to make sure sensor responds properly
            _ = bme.temperature
            _ = bme.humidity
            print(f"✅ BME280 {sensor_name} OK en 0x{addr:02x}")
            sensor_status[sensor_name]["ok"] = True

            # Assign to correct global
            if addr == 0x76:
                bme280_in = bme
            else:
                bme280_out = bme
        except Exception as e:
            error_msg = str(e)
            print(f"⚠️ BME280 {sensor_name} responde mal en 0x{addr:02x}: {e}")
            sensor_status[sensor_name]["error"] = error_msg
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

def read_sensors(max_retries=3, retry_delay=2):
    """
    Read sensors with retry logic and I2C recovery
    
    Parameters:
    - max_retries: Maximum number of retry attempts
    - retry_delay: Delay between retries in seconds
    """
    global bme280_in, bme280_out
    
    for attempt in range(max_retries):
        json_data = {}
        
        # Check if sensors are initialized
        if bme280_in is None or bme280_out is None:
            print(f"⚠️ Sensors not initialized, attempting reinit (attempt {attempt + 1}/{max_retries})")
            if check_and_init_sensors():
                continue  # Retry reading
            else:
                sleep(retry_delay)
                continue
        
        try:
            # Read from BME280 sensors
            temperature_c = bme280_in.temperature
            humidity = bme280_in.relative_humidity
            outside_temperature_c = bme280_out.temperature   
            outside_humidity = bme280_out.relative_humidity
            
            # Validate readings
            if any(reading is None for reading in [temperature_c, humidity, outside_temperature_c, outside_humidity]):
                raise RuntimeError("Invalid sensor reading (None values)")
                
            json_data['temperature'] = round(temperature_c, 2)
            json_data['humidity'] = round(humidity, 2)
            json_data['outside_temperature'] = round(outside_temperature_c, 2)
            json_data['outside_humidity'] = round(outside_humidity, 2)
            json_data['vpd'] = calculate_vpd(temperature_c, humidity)
            return json_data
            
        except RuntimeError as error:
            print(f"Error reading sensor (attempt {attempt + 1}/{max_retries}): {error}")
            if attempt < max_retries - 1:
                sleep(retry_delay)
            continue
            
        except OSError as error:
            # OSError often indicates I2C bus issues
            print(f"🚨 I2C communication error (attempt {attempt + 1}/{max_retries}): {error}")
            
            # Reset sensor references and try recovery
            bme280_in = None
            bme280_out = None
            
            if attempt < max_retries - 1:
                print("🔄 Attempting I2C recovery...")
                if check_and_init_sensors():
                    print("✅ Sensors recovered, retrying read...")
                    continue
                sleep(retry_delay)
            continue
            
        except Exception as error:
            print(f"Unexpected error: {error}")
            # For unexpected errors, try recovery once
            bme280_in = None
            bme280_out = None
            if attempt < max_retries - 1:
                check_and_init_sensors()
                sleep(retry_delay)
            continue
    
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

    # Initialize I2C bus first
    init_i2c_bus()

    # Check sensors before starting - retry instead of exiting
    while not check_and_init_sensors():
        print("❌ No se pueden inicializar los sensores BME280. Reintentando en 5 segundos...")
        sleep(5)
    
    # Keep track of current stage to detect changes
    current_stage = None
    current_grow_id = None
    stage_check_counter = 0
    STAGE_CHECK_INTERVAL = 20  # Check for stage changes every 20 iterations (~60 seconds)
    
    # Control for SQLite storage (every 5 minutes minimum)
    last_db_save_time = 0
    DB_SAVE_INTERVAL = 300  # 5 minutes in seconds
    
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
                
                STAGE = current_stage
            
            # Increment counter and reset when reaching interval
            stage_check_counter = (stage_check_counter + 1) % STAGE_CHECK_INTERVAL
            
            sensors_data = read_sensors()
            if sensors_data is None:
                # Immediately try recovery - no waiting, no counting
                print("⚠️ No valid sensor data - attempting immediate recovery...")
                all_outputs_off()  # Safety: turn off all outputs
                recover_i2c_bus()
                check_and_init_sensors()
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
                
                if sensors_data['vpd_in_range']:
                    redis_client.set('sensors', json.dumps(sensors_data))
                    sleep(3)
                    continue
            else:
                sensors_data['vpd_in_range'] = False
                if humidity >= 60 and humidity <= 65:
                    target_humidity = humidity
                    humidity_is_in_range = True
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
                sleep(3)
                continue

            if target_humidity is None:
                continue
            if humidity < target_humidity:
                humidity_up_on()
                humidity_down_off()
                if outside_humidity > target_humidity:
                    ventilation_on()
                else:
                    ventilation_off()
            elif humidity > target_humidity:
                humidity_up_off()
                humidity_down_on()
                if outside_humidity < target_humidity:
                    ventilation_on()
                else:
                    ventilation_off()
            elif humidity == target_humidity:
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

