import datetime
import logging
import threading
import time
from asyncio import run
from threading import Timer


class RainSensor:
    def __init__(self, mqttc, channel):
        self.mqttc= mqttc
        self.channel=channel

    def get_rain_status(self):
        # TODO: implement via MQTT
        return False


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
        self.logger = logger or logging.getLogger(__name__)

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

    def turn_on(self, seconds) -> "SprinklerRun | None":
        try:
            if seconds > self.failsafe_max:
                seconds = self.failsafe_max

            run = SprinklerRun(run_time=seconds, sprinkler=self, logger=self.logger)
            run.run()
            return run

        except Exception as e:
            self.logger.error(f"Error turning on sprinkler {self.name}: {e}")
            return None

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

    def remaining_time(self, run=None):
        # Return remaining time from the provided active SprinklerRun, or 0 if none
        return run.remaining_time if run is not None else 0


class SprinklerRun:
    def __init__(self, run_time, sprinkler: Sprinkler, logger=None):
        
        self.logger = logger or logging.getLogger(__name__)

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
        try:
            sp = self.sprinkler
            if not sp.dummy:
                sp.mqttc.set_channel(sp.channel, 1)
                sp.logger.info(
                    f"Turning on sprinkler {sp.name} (channel {sp.channel}) for {self.run_time} seconds"
                )
            else:
                sp.logger.info(
                    f"[DUMMY] Turning on sprinkler {sp.name} (channel {sp.channel}) for {self.run_time} seconds"
                )
            sp.state = 1
            sp.last_activation = datetime.datetime.now()
            self._start_countdown()
        except Exception as e:
            self.logger.exception("Run failed: %s", e)
            self._finish(state=SPRINKLER_RUN_STATE[4])  # FAILED
            raise


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
                    self._timer.daemon = True
                    self._timer.start()

        self._timer = threading.Timer(1, countdown)

        self.started_at = datetime.datetime.now()
        self._timer.start()

        self.state = SPRINKLER_RUN_STATE[1]  # running

    # call this when countdown naturally hits zero
    def _destroy(self):
        self.logger.info(
            f"Destroying SprinklerRun for {self.sprinkler.name}. Final remaining_time: {self.remaining_time}"
        )
        self._finish(state=SPRINKLER_RUN_STATE[2])       # COMPLETED

    def stop(self):
        # Stop the run externally
        self._finish(state=SPRINKLER_RUN_STATE[3])      # TERMINATED    

    def _finish(self, state):
        """Idempotent cleanup: cancel timers, turn off sprinkler, set state & event."""
        if getattr(self, "_active", False):
            self._active = False
            try:
                if self._timer:
                    self._timer.cancel()
            finally:
                self._timer = None
            # Ensure valve is off
            try:
                if getattr(self.sprinkler, "state", 0) == 1:
                    self.sprinkler.turn_off()
            except Exception as e:
                self.logger.warning("turn_off failed during cleanup: %s", e)
            self.state = state
            self.done.set()

SPRINKLER_RUN_STATE = {
    0: "SCHEDULED",
    1: "RUNNING",
    2: "COMPLETED",
    3: "TERMINATED",
    4: "FAILED",
}


