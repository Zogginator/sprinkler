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
        self.logger = logger

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

        self.dummy = dummy  #testing with dummy sprinklers, without starting real ones

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
        self.logger=logger

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
        self.done = threading.Event()

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
        if getattr(self.sprinkler, "state", 0) == 1:
            self.sprinkler.turn_off()
        self.done.set()
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
    
    def run_sequentially(runs, delay_seconds=2):
        for r in runs:
            r.run()
            last_len = 0
            # r.done.wait()           # wait until this run finishes.replce below with this if printing progress not needed
            try:
                while not r.done.wait(timeout=1):
                    rem = max(0, int(getattr(r, "remaining_time", 0)))
                    msg = f"Running {r.sprinkler.name}: {rem:02d}s remaining"
                    print("\r" + msg + " " * max(0, last_len - len(msg)), end="", flush=True)
                    last_len = len(msg)
            finally:
                # clear the line and report completion
                print("\r" + " " * last_len + "\r", end="")
                print(f"{r.sprinkler.name}: finished")
            time.sleep(delay_seconds)
    
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


    run_sequentially(runs, delay_seconds=2)
    print("All runs completed.")


