import RPi.GPIO as GPIO
from time import sleep


def main():
    GPIO.output(24, 0)
    sleep(60)  # Wait for 10 seconds

if __name__ == "__main__":
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(24, GPIO.OUT)
        main()
        GPIO.output(24, 1)
    finally:
        GPIO.cleanup(24)
