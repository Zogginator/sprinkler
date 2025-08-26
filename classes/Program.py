from Sprinkler import SprinklerRun


class Program:
    def __init__(self, id, name, sprinklers, runtimes, logger=None):
        if self.logger is None:
            import logging

            logging.basicConfig(level=logging.DEBUG)
            logger = logging.getLogger(__name__)
        self.logger = logger

        self.id = id
        self.name = name
        self.sprinkers = sprinklers  # list of sprinkler objects
        self.runtimes = runtimes  # list of (sprinkler_id, runtime) tuples

        # Dictionary mapping sprinkler IDs to sprinkler objects for quick lookup
        self.SPRINKLERS_BY_ID = {s.id: s for s in self.sprinkers}

    def get_runs(self) -> list[SprinklerRun]:
        runs = []
        # Iterate over each (sprinkler_id, runtime) tuple
        for run_id, runtime in self.runtimes:
            # Find the sprinkler object by its ID
            sprinkler = self.SPRINKLERS_BY_ID.get(run_id)
            if sprinkler:
                # Create a SprinklerRun object if the sprinkler exists
                run = SprinklerRun(runtime, sprinkler, logger=self.logger)
                runs.append(run)
        # Return the list of SprinklerRun objects
        return runs
