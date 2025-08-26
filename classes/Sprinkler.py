import datetime
import logging
import threading
import time
from asyncio import run
from threading import Timer


class Sprinkler:
    def __init__(
        self,
        mqttc,
        zones_by_id,
        name,
        channel,
        id,
        failsafe_max=600,
        poll_seconds=3,
        dummy=False,
        logger=None,
    ):
        if logger is None:
            logging.basicConfig(level=logging.DEBUG)
            logger = logging.getLogger(__name__)

        # Initialize sprinkler attributes
        self.mqttc = mqttc
        self.zones_by_id = zones_by_id
        self.name = name
        self.channel = channel
        self.id = id
        self.failsafe_max = failsafe_max
        self.poll_seconds = poll_seconds

        self.state = 0  # 0=off, 1=on
        self.last_activation = None
        self.last_termination = None

        self.dummy = dummy

    def turn_on(self, seconds):
        try:
            # Limit activation time to failsafe_max
            if seconds > self.failsafe_max:
                seconds = self.failsafe_max

            if not self.dummy:
                # Activate hardware channel
                self.mqttc.set_channel(self.channel, 1)
                self.logger.info(
                    f"Turning on sprinkler {self.name} (channel {self.channel}) for {seconds} seconds"
                )
            else:
                # Simulate activation in dummy mode
                self.logger.info(
                    f"[DUMMY] Turning on sprinkler {self.name} (channel {self.channel}) for {seconds} seconds"
                )
            self.state = 1
            self.last_activation = datetime.datetime.now()

            # Schedule automatic turn off after 'seconds'
            Timer(seconds, self.turn_off).start()
            return True

        except Exception as e:
            # Handle errors during activation
            self.logger.error(f"Error turning on sprinkler {self.name}: {e}")
            return False

    def turn_off(self):
        try:
            if not self.dummy:
                # Deactivate hardware channel
                self.mqttc.set_channel(self.channel, 0)
                self.logger.info(
                    f"Turning off sprinkler {self.name} (channel {self.channel})"
                )
            else:
                # Simulate deactivation in dummy mode
                self.logger.info(
                    f"[DUMMY] Turning off sprinkler {self.name} (channel {self.channel})"
                )

            self.state = 0
            self.last_termination = datetime.datetime.now()

        except Exception as e:
            # Handle errors during deactivation
            self.logger.error(f"Error turning off sprinkler {self.name}: {e}")

    def remaining_time(self):
        # Return remaining time for the first run if exists
        return self.runs[0].remaining_time if self.runs else 0


class SprinklerRun:
    def __init__(self, run_time, sprinkler: Sprinkler, logger=None):
        if logger is None:
            logging.basicConfig(level=logging.DEBUG)
            logger = logging.getLogger(__name__)

        # Initialize run attributes
        self.sprinkler = sprinkler
        self.run_time = run_time
        self.remaining_time = run_time

        self._timer = None
        self._lock = threading.Lock()
        self._active = True

        self.state = SPRINKLER_RUN_STATE[
            0
        ]  # 0=scheduled, 1=running, 2=completed, 3=terminated, 4=failed
        self.created_at = datetime.datetime.now()
        self.started_at = None

    def run(self):
        # Start countdown and activate sprinkler
        self.sprinkler.logger.info(
            f"Starting SprinklerRun for {self.sprinkler.name} for {self.run_time} seconds."
        )
        self._start_countdown()
        self.sprinkler.turn_on(self.run_time)

    def _start_countdown(self):
        # Internal countdown function, decrements remaining_time every second
        def countdown():
            with self._lock:
                if not self._active:
                    return
                self.remaining_time -= 1
                if self.remaining_time <= 0:
                    # Stop timer when finished
                    self._destroy()
                else:
                    # Schedule next countdown tick
                    self._timer = threading.Timer(1, countdown)
                    self._timer.start()

        self._timer = threading.Timer(1, countdown)

        self.started_at = datetime.datetime.now()
        self._timer.start()

        self.state = SPRINKLER_RUN_STATE[1]  # running

    def _destroy(self):
        # Stop countdown and cleanup
        self.sprinkler.logger.info(
            f"Destroying SprinklerRun for {self.sprinkler.name}. Final remaining_time: {self.remaining_time}"
        )
        self._active = False
        if self._timer:
            self._timer.cancel()
        # Optionally, add cleanup code here

    def stop(self):
        # Stop the run externally
        with self._lock:
            self._destroy()


SPRINKLER_RUN_STATE = {
    0: "SCHEDULED",
    1: "RUNNING",
    2: "COMPLETED",
    3: "TERMINATED",
    4: "FAILED",
}

if __name__ == "__main__":
    print("Testing SprinklerRun...")

    # Create dummy sprinkler instances for testing
    sprinklers = [
        Sprinkler(None, None, "Test Sprinkler", 1, 1, dummy=True),
        Sprinkler(None, None, "Test Sprinkler 2", 2, 2, dummy=True),
    ]
    runs = []
    # Schedule two runs for sprinklers
    runs.append(SprinklerRun(10, sprinklers[0]))
    runs.append(SprinklerRun(10, sprinklers[1]))

    # Start first run
    runs[0].run()
    time.sleep(2)
    # Start second run after 2 seconds
    runs[1].run()

    # Monitor active runs and print remaining times
    while True:
        time.sleep(1)
        remaining = [r.remaining_time for r in runs if r._active]
        runtimes = [r.run_time for r in runs if r._active]
        print("Active runs remaining times:", remaining)
        print("Active runs total times:", runtimes)
        if len(remaining) == 0:
            print("No active runs left, exiting.")
            break
