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
from database import store_control_event, store_sensor_sample

HUMIDITY_CONTROL_PIN_UP = 25
HUMIDITY_CONTROL_PIN_DOWN = 16
VENTILATION_CONTROL_PIN = 7


EARLY_VEG_VPD_RANGE = (0.6, 1.0)
LATE_VEG_VPD_RANGE = (0.8, 1.2)
FLOWERING_VPD_RANGE = (1.2, 1.5)

redis_client = redis.Redis(host='localhost', port=6379, db=0)

# Initialize I2C bus
i2c = busio.I2C(board.SCL, board.SDA)

# Initialize BME280 sensors with different addresses
# Indoor sensor: address 0x76 (SD0 connected to GND)
bme280_in = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=0x76)
# Outdoor sensor: address 0x77 (SD0 connected to VCC)
bme280_out = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=0x77)

humidity_control_up = None
humidity_control_down = None
ventilation_control = None


def setup_gpio():
    global humidity_control_up
    global humidity_control_down
    global ventilation_control
    try:
        humidity_control_up = gpiozero.OutputDevice(HUMIDITY_CONTROL_PIN_UP, active_high=True, initial_value=False)
        humidity_control_down = gpiozero.OutputDevice(HUMIDITY_CONTROL_PIN_DOWN, active_high=True, initial_value=False)
        ventilation_control = gpiozero.OutputDevice(VENTILATION_CONTROL_PIN, active_high=True, initial_value=False)
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
    Read sensors with retry logic
    
    Parameters:
    - max_retries: Maximum number of retry attempts
    - retry_delay: Delay between retries in seconds
    """
    for attempt in range(max_retries):
        json_data = {}
        try:
            # Read from BME280 sensors
            temperature_c = bme280_in.temperature
            humidity = bme280_in.relative_humidity
            outside_temperature_c = bme280_out.temperature   
            outside_humidity = bme280_out.relative_humidity
            
            # Validate readings
            if any(reading is None for reading in [temperature_c, humidity, outside_temperature_c, outside_humidity]):
                raise RuntimeError("Invalid sensor reading")
                
            json_data['temperature'] = round(temperature_c, 2)
            json_data['humidity'] = round(humidity, 2)
            json_data['outside_temperature'] = round(outside_temperature_c, 2)
            json_data['outside_humidity'] = round(outside_humidity, 2)
            json_data['vpd'] = calculate_vpd(temperature_c, humidity)
            return json_data
            
        except RuntimeError as error:
            print("Error reading sensor (attempt {}/{}: {}".format(attempt + 1, max_retries, error))
            if attempt < max_retries - 1:
                sleep(retry_delay)
            continue
        except Exception as error:
            print("Unexpected error: {}".format(error))
            return None
    
    print("Failed to read sensors after maximum retries")
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

def main(stage):
    setup_gpio()
    STAGE = stage if stage in ["early_veg", "late_veg", "flowering", "dry"] else "early_veg"
    
    while True:
        try:
            sensors_data = read_sensors()
            if sensors_data is None:
                print("No valid sensor data, retrying in 3 seconds...")
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
            if stage != "dry":
                if vpd_is_in_range(leaf_vpd, STAGE):
                    sensors_data['leaf_temperature'] = leaf_temperature
                    sensors_data['leaf_vpd'] = leaf_vpd
                    redis_client.set('sensors', json.dumps(sensors_data))
                    sleep(3)
                    continue
                target_humidity = calculate_target_humidity(STAGE, temperature)
                sensors_data['target_humidity'] = target_humidity
                sensors_data['leaf_temperature'] = leaf_temperature
                sensors_data['leaf_vpd'] = leaf_vpd
            else:
                if humidity >= 60 and humidity <= 65:
                    target_humidity = humidity
                    humidity_is_in_range = True
                elif humidity >= 65:
                    target_humidity = 60
                elif humidity <= 60:
                    target_humidity = 65
            sensors_data['target_humidity'] = target_humidity
            sensors_data['leaf_temperature'] = leaf_temperature
            sensors_data['leaf_vpd'] = leaf_vpd
            
            # Store in Redis for real-time access
            redis_client.set('sensors', json.dumps(sensors_data))
            
            # Store in SQLite for historical persistence
            store_sensor_sample(sensors_data)
            
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
            print("Error in main loop: {}".format(e))
            sleep(3)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        stage = sys.argv[1]
        main(stage)
    else:
        main("early_veg")
