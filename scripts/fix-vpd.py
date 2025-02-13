import json
import math
import sys
from time import sleep

import adafruit_dht
import board
import gpiozero
import redis

HUMIDITY_CONTROL_PIN_UP = 25
HUMIDITY_CONTROL_PIN_DOWN = 16
VENTILATION_CONTROL_PIN = 7


EARLY_VEG_VPD_RANGE = (0.4, 0.8)
LATE_VEG_VPD_RANGE = (0.8, 1.2)
FLOWERING_VPD_RANGE = (1.2, 1.6)

redis_client = redis.Redis(host='localhost', port=6379, db=0)
dhtDevice_in = adafruit_dht.DHT22(board.D10)
dhtDevice_out = adafruit_dht.DHT22(board.D11)

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
        print(f"Error setting up GPIO: {str(e)}")
        raise

def humidity_up_on():
    try:
        humidity_control_up.on()
    except Exception as e:
        print(f"Error controlling GPIO: {str(e)}")

def humidity_up_off():
    try:
        humidity_control_up.off()
    except Exception as e:
        print(f"Error controlling GPIO: {str(e)}")

def humidity_down_on():
    try:
        humidity_control_down.on()
    except Exception as e:
        print(f"Error controlling GPIO: {str(e)}")

def humidity_down_off():
    try:
        humidity_control_down.off()
    except Exception as e:
        print(f"Error controlling GPIO: {str(e)}")

def ventilation_on():
    try:
        ventilation_control.on()
    except Exception as e:
        print(f"Error controlling GPIO: {str(e)}")

def ventilation_off():
    try:
        ventilation_control.off()
    except Exception as e:
        print(f"Error controlling GPIO: {str(e)}")

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

def read_sensors():
    json_data = {}
    try:
        temperature_c = dhtDevice_in.temperature
        humidity = dhtDevice_in.humidity
        outside_temperature_c = dhtDevice_out.temperature   
        outside_humidity = dhtDevice_out.humidity
        json_data['temperature'] = temperature_c
        json_data['humidity'] = humidity
        json_data['outside_temperature'] = outside_temperature_c
        json_data['outside_humidity'] = outside_humidity
        json_data['vpd'] = calculate_vpd(temperature_c, humidity)
        print(
            "Temp: {:.1f} C    Humidity: {}%    VPD: {:.2f} kPa    Outside Temp: {:.1f} C    Outside Humidity: {}%".format(
                temperature_c, humidity, json_data['vpd'], outside_temperature_c, outside_humidity
            )
        )
        redis_client.set('sensors', json.dumps(json_data))
    except RuntimeError as error:
        print(error.args[0])
        return None
    except Exception as error:
        dhtDevice_in.exit()
        dhtDevice_out.exit()
        raise error
    return json_data

def main(stage):
    setup_gpio()
    STAGE = stage if stage in ["early_veg", "late_veg", "flowering"] else "early_veg"
    while True:
        sensors_data = read_sensors()
        print("\n")
        print(f"--------------------------------")
        if sensors_data is None:
            print("Error reading sensors")
            continue
        temperature = float(sensors_data['temperature'])
        humidity = float(sensors_data['humidity'])
        vpd = float(sensors_data['vpd'])
        outside_temperature = float(sensors_data['outside_temperature'])
        outside_humidity = float(sensors_data['outside_humidity'])
        if vpd_is_in_range(vpd, STAGE):
            print(f"VPD is in range: {vpd}")
            sleep(3)
            continue
        target_humidity = calculate_target_humidity(STAGE, temperature)
        print(f"Target humidity: {target_humidity}")
        if target_humidity is None:
            print("Error calculating target humidity")
            continue
        if humidity < target_humidity:
            print("Humidity is less than target humidity")
            humidity_up_on()
            humidity_down_off()
            if outside_humidity > target_humidity:
                ventilation_on()
            else:
                ventilation_off()
        elif humidity > target_humidity:
            print("Humidity is greater than target humidity")
            humidity_up_off()
            humidity_down_on()
            if outside_humidity < target_humidity:
                ventilation_on()
            else:
                ventilation_off()
        elif humidity == target_humidity:
            print("Humidity is equal to target humidity")
            humidity_up_off()
            humidity_down_off()
            ventilation_off()
        print(f"--------------------------------")
        sleep(3)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        stage = sys.argv[1]
        main(stage)
