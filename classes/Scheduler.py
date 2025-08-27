import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler


from classes.Program import Program


class StartTime:
    def __init__(self, hour: int, minute: int):
        self.hour = hour
        self.minute = minute


class DayOption:
    def __init__(self, name, start_time: StartTime, program: Program, day=None):
        self.name = name
        self.start_time = start_time
        self.day = day  # Day of week (optional)
        self.program = program



class Scheduler:
    def __init__(self, day_options: list[DayOption], runs_list, logger=None):
        if logger is None:
            logging.basicConfig(level=logging.DEBUG)
            logger = logging.getLogger(__name__)
        self.logger = logger

        self.day_options = day_options
        self.runs_list = runs_list

        self.scheduler = BackgroundScheduler()  # Create background scheduler

        for day_opt in self.day_options:
            if day_opt.day is None:
                # If no specific day is set, schedule job to run every day at the specified time
                self.scheduler.add_job(
                    self.start_program,
                    "cron",
                    hour=day_opt.start_time.hour,
                    minute=day_opt.start_time.minute,
                    args=[day_opt],
                )
                logger.debug(
                    f"Scheduled {day_opt.name} to run every day at {day_opt.start_time.hour}:{day_opt.start_time.minute}"
                )
            else:
                # Schedule job for a specific day of the week at the specified time
                self.scheduler.add_job(
                    self.start_program,
                    "cron",
                    hour=day_opt.start_time.hour,
                    minute=day_opt.start_time.minute,
                    day_of_week=day_opt.day,
                    args=[day_opt],
                )
                logger.debug(
                    f"Scheduled {day_opt.name} to run on {day_opt.day} at {day_opt.start_time.hour}:{day_opt.start_time.minute}"
                )

    def start_program(self, day_opt: DayOption):
        # Get all runs from the program
        runs = day_opt.program.get_runs()
        self.logger.info(
            f"Starting program '{day_opt.name}' with {len(runs)} runs.\n"
            + f"starting at: {datetime.now()}\n"
            + f"scheduled time: {day_opt.start_time.hour}:{day_opt.start_time.minute}"
        )
        self.runs_list.extend(runs)  # Add runs to the shared list
        for run in runs:
            run.run(run.run_time)
