from classes.Sprinkler import Sprinkler, SprinklerRun
import time 
import logging
from weakref import WeakSet
import os, yaml
from app_runtime import SPRINKLER_BY_ID

def program_constructor(id, name, runtimes):
    return Program(id, name, runtimes)


def program_constructor_from_db(program_id):
    return Program('1', 'test', [(3, 10), (2,10)])



class Program:
    def __init__(self, id, name, runtimes, sprinkler_by_id=SPRINKLER_BY_ID, logger=None):
        
        self.logger = logger or logging.getLogger(__name__)

        self.id = id
        self.name = name
        self.sprinkler_by_id = sprinkler_by_id       # {1: <Sprinkler object 1>, 2: <Sprinkler object 1>, 3: <Sprinkler object 1>}
        self.runtimes = runtimes  # list of (sprinkler_id, runtime) tuples eg. [(2, 30), (3, 30)]
        # for cleanup
        self._active_runs = WeakSet()   # track runs we created
        self._closed = False

       
    
    def get_runs(self) -> list[SprinklerRun]:           #[<classes.Sprinkler.SprinklerRun object 1>, <classes.Sprinkler.SprinklerRun object 2]
        runs = []
        # Iterate over each (sprinkler_id, runtime) tuple
        for run_id, runtime in self.runtimes:
            # Find the sprinkler object by its ID
            sprinkler = self.sprinkler_by_id .get(run_id)
            if sprinkler:
                # Create a SprinklerRun object if the sprinkler exists
                run = SprinklerRun(runtime, sprinkler, logger=self.logger)
                runs.append(run)
        # Return the list of SprinklerRun objects
        return runs
    
    def run_sequentially(self, delay_seconds=2): # TODO manage delay-setting
        runs = self.get_runs()   
        for r in runs:
            
            last_len = 0
            # r.done.wait()           # wait until this run finishes.replace below with this if printing progress not needed
            try:
                r.run()
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

    def cleanup(self):
        """Idempotent teardown: stop any leftover runs and drop heavy refs."""
        if self._closed:
            return
        self._closed = True
        # ensure every created run is finished
        for r in list(self._active_runs):
            try:
                r.stop()        # calls the run's _finish(); safe if already done
            except Exception as e:
                self.logger.warning("Run cleanup failed: %s", e)
        self._active_runs.clear()

        # drop references so GC can collect the Program quickly
        self.runtimes = None
        self.sprinkler_by_id = None

    # optional: context-manager sugar so cleanup always runs
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        self.cleanup()
        return False 