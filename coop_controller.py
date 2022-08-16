import os
import json
from argparse import ArgumentParser
from time import sleep
from dataclasses import dataclass, InitVar
import logging.config
import datetime
import traceback

from suntime import Sun
import RPi.GPIO as GPIO


@dataclass
class CoopController:
    config_args: InitVar[dict]

    # states and modes
    OPEN = "open"
    CLOSED = "closed"
    AUTO = "auto"
    MANUAL = "manual"

    # state/mode vars + paths for persistance
    state: str = None
    mode: str = None
    state_file: str = "door_state"
    mode_file: str = "door_mode"
    gpio_is_setup: bool = False

    def __post_init__(self, args):
        # read config file
        self.CONFIG_FILE: str = args.CONFIG_FILE
        if os.path.exists(self.CONFIG_FILE):
            with open(self.CONFIG_FILE) as f:
                self.CONFIG = json.load(f)
        else:
            raise Exception(f"Error opening config file {self.CONFIG_FILE}")

        # simulation mode
        self.sim: bool = args.SIM

        # assign config items to object attributes
        for k, v in self.CONFIG.items():
            setattr(self, k, v)

        # set up config
        if hasattr(self, "LOG_CONFIG") and self.LOG_CONFIG:
            logging.config.dictConfig(self.LOG_CONFIG)
        else:
            logging.basicConfig(level="INFO")
        self.logger = logging.getLogger(__name__)

        self.logger.info("Read config file: %s", self.CONFIG_FILE)
        for k, v in self.CONFIG.items():
            self.logger.info("Config: %s = %s", k, v)

        # init state and such
        self.logger.info("Initializing controller")
        self.door_states = (self.OPEN, self.CLOSED)
        self.door_modes = (self.AUTO, self.MANUAL)
        self.state = self.check_door_state()
        self.mode = self.check_door_mode()
        self.logger.info("Door state: %s", self.state)
        self.logger.info("Door mode: %s", self.mode)

    def init_gpio(self):
        self.logger.info("Initializing GPIO")
        if not self.sim:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.IN1, GPIO.OUT)
            GPIO.setup(self.IN2, GPIO.OUT)
            GPIO.setup(self.EN, GPIO.OUT)
            GPIO.output(self.IN1, GPIO.LOW)
            GPIO.output(self.IN2, GPIO.LOW)
            self.p = GPIO.PWM(self.EN, self.FREQUENCY)
            self.p.start(self.DUTY_CYCLE)
        self.gpio_is_setup = True

    def check_door_state(self) -> str:
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                state = f.readline().strip()
                if state in self.door_states:
                    return state
        else:
            self.logger.error("Door state is not set, opening the door")
            self.open_door()
            return "open"

    def set_door_state(self, state: str) -> bool:
        self.logger.info(f"Setting state: {state}")
        if state in self.door_states:
            self.state = state
            with open(self.state_file, "w") as f:
                f.write(state + "\n")
            return True
        else:
            # raise?
            return False

    def check_door_mode(self) -> str:
        if os.path.exists(self.mode_file):
            with open(self.mode_file) as f:
                mode = f.readline().strip()
                if mode in self.door_modes:
                    return mode
        else:
            self.logger.error("Door mode is not set, setting to auto")
            self.set_door_mode("auto")
            return "auto"

    def set_door_mode(self, mode) -> bool:
        self.logger.info(f"Setting mode: {mode}")
        if mode in self.door_modes:
            self.mode = mode
            with open(self.mode_file, "w") as f:
                f.write(mode + "\n")
            return True
        else:
            # raise?
            return False

    def calculate_sunrise_and_sunset(self):
        self.logger.info("Calculating sunrise and sunset times")
        sun = Sun(55.4, 12.3)
        self.sunrise = sun.get_local_sunrise_time()
        self.sunset = sun.get_local_sunset_time()
        self.sunset_with_buffer = self.sunset + datetime.timedelta(
            seconds=self.BUFFER_AFTER_SUNSET
        )
        self.logger.info(f"Sunrise: {self.sunrise}")
        self.logger.info(f"Sunset: {self.sunset}")
        self.logger.info(f"Sunset with buffer: {self.sunset_with_buffer}")

        # check earliest open time
        self.logger.info("Checking earliest open time")
        self.earliest_open = None
        self.local_tz = datetime.datetime.now().astimezone().tzinfo
        if self.EARLIEST_OPEN:
            try:
                hour, minute, second = (int(x) for x in self.EARLIEST_OPEN.split(":"))
                self.earliest_open_time = datetime.time(
                    hour, minute, second, tzinfo=self.local_tz
                )
                self.logger.info(
                    "Earliest open: %s", self.earliest_open_time.isoformat()
                )
            except Exception:
                self.logger.error(
                    "Failed to parse %s, format should be HH:MM:SS", self.EARLIEST_OPEN
                )

    def open_door(self):
        self.logger.info("Opening door")

        if not self.gpio_is_setup:
            return

        # run motor in backward direction
        if not self.sim:
            GPIO.output(self.IN1, GPIO.LOW)
            GPIO.output(self.IN2, GPIO.HIGH)

        sleep(self.TIME_TO_OPEN)

        # stop motor
        if not self.sim:
            GPIO.output(self.IN1, GPIO.LOW)
            GPIO.output(self.IN2, GPIO.LOW)

        self.set_door_state("open")
        self.logger.info("Door opened")

    def close_door(self):
        self.logger.info("Closing door")

        if not self.gpio_is_setup:
            return

        # run motor in forward direction
        if not self.sim:
            GPIO.output(self.IN1, GPIO.HIGH)
            GPIO.output(self.IN2, GPIO.LOW)

        sleep(self.TIME_TO_OPEN)

        # stop motor
        if not self.sim:
            GPIO.output(self.IN1, GPIO.LOW)
            GPIO.output(self.IN2, GPIO.LOW)

        self.set_door_state("closed")
        self.logger.info("Door closed")

    def run(self):
        while 1:
            try:
                saved_mode = self.check_door_mode()
                if self.mode != saved_mode:
                    self.set_door_mode(saved_mode)

                if self.mode == "auto":
                    now = datetime.datetime.now().astimezone()
                    if (
                        not hasattr(self, "today")
                        or self.today != datetime.date.today()
                    ):
                        self.today = datetime.date.today()
                        self.logger.info(f"Setting date to {self.today}")
                        self.logger.info(f"Current time: {now}")
                        self.calculate_sunrise_and_sunset()

                    saved_state = self.check_door_state()
                    if self.state != saved_state:
                        self.set_door_state(saved_state)

                    if now < self.sunrise and self.state == "open":
                        self.logger.info("Sun is not up yet")
                        self.close_door()
                    elif (
                        self.sunrise < now < self.sunset_with_buffer
                        and now.timetz() > self.earliest_open_time
                        and self.state == "closed"
                    ):
                        self.logger.info("Sun is up")
                        self.open_door()
                    elif self.sunset_with_buffer < now and self.state == "open":
                        self.logger.info("Sun has set")
                        self.close_door()
                    else:
                        self.logger.debug("Nothing to do")
                elif self.mode == "manual":
                    saved_state = self.check_door_state()
                    if saved_state != self.state:
                        if saved_state == "open":
                            self.logger.info("Manual open")
                            self.open_door()
                        elif saved_state == "closed":
                            self.logger.info("Manual close")
                            self.close_door()
            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt detected")
                self.logger.info("Cleaning up")
                if not self.sim:
                    GPIO.cleanup()
                break
            except Exception:
                self.logger.error(traceback.format_exc())
            sleep(self.SLEEP_DURATION)


if __name__ == "__main__":  # pragma: no cover
    parser = ArgumentParser()
    parser.add_argument(
        "-c",
        "--config",
        dest="CONFIG_FILE",
        help="Path to config file",
        default="/etc/door_controller/door_controller.conf",
    )
    parser.add_argument(
        "-m",
        "--mode",
        dest="MODE",
        choices=["standby", "open", "close", "manual", "auto"],
        help="Run mode",
    )
    parser.add_argument(
        "-s",
        "--simulate",
        action="store_true",
        dest="SIM",
        help="Simulate only (don't interact with motor)",
    )
    args = parser.parse_args()

    door_controller = CoopController(args)
    if args.MODE == "standby":
        door_controller.init_gpio()
        door_controller.set_door_mode(CoopController.AUTO)
        door_controller.run()
    elif args.MODE == "open":
        door_controller.set_door_mode(CoopController.MANUAL)
        door_controller.set_door_state(CoopController.OPEN)
    elif args.MODE == "close":
        door_controller.set_door_mode(CoopController.MANUAL)
        door_controller.set_door_state(CoopController.CLOSED)
    elif args.MODE == "manual":
        door_controller.set_door_mode(CoopController.MANUAL)
    elif args.MODE == "auto":
        door_controller.set_door_mode(CoopController.AUTO)
