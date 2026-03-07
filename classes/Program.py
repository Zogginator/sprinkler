import logging
import time


def program_constructor(id, name, runtimes):
    return Program(id, name, runtimes)


def program_constructor_from_db(program_id):
    raise NotImplementedError("program_constructor_from_db: DB persistence not yet implemented")


class Program:
    def __init__(self, id, name, runtimes, sprinkler_by_id=None, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        self.id = id
        self.name = name
        self.runtimes = runtimes  # list of (zone_id, seconds) tuples
        self.sprinkler_by_id = sprinkler_by_id  # falls back to app_runtime.SPRINKLER_BY_ID

    def run_sequentially(self, delay_seconds=2, on_step_start=None, stop_event=None):
        import app_runtime
        spr_by_id = self.sprinkler_by_id or app_runtime.SPRINKLER_BY_ID
        for zone_id, duration in self.runtimes:
            if stop_event and stop_event.is_set():
                break
            sp = spr_by_id.get(zone_id)
            if sp is None:
                self.logger.warning("Zone %d not found, skipping", zone_id)
                continue
            sp.turn_on(duration)
            if on_step_start:
                on_step_start()
            # Sleep for duration, checking stop_event every second for abort support
            elapsed = 0
            while elapsed < duration:
                if stop_event and stop_event.is_set():
                    break
                time.sleep(1)
                elapsed += 1
            sp.turn_off()
            if delay_seconds and not (stop_event and stop_event.is_set()):
                time.sleep(delay_seconds)
