from datetime import datetime
from time import sleep

import gpiozero

# GPIO setup using board
WATER_PUMP_PIN = 23  # Usando D14 que corresponde al GPIO23
water_control = None

def setup_gpio():
    global water_control
    try:
        relay = gpiozero.OutputDevice(WATER_PUMP_PIN, active_high=True, initial_value=False)
        water_control = relay
    except Exception as e:
        print(f"Error setting up GPIO: {str(e)}")
        raise

def water_on():
    try:
        water_control.on()
        sleep(10)  # Wait for 10 seconds
        water_control.off()
    except Exception as e:
        print(f"Error controlling GPIO: {str(e)}")
        raise
    finally:
        if water_control:
            water_control.close()

def main():
    setup_gpio()
    water_on()

if __name__ == "__main__":
    main()
