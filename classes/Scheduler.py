from __future__ import annotations
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo    
TZ = ZoneInfo("Europe/Budapest")   # YAML-ból kéne átvennie

from classes.Program import program_constructor_from_db

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore



class StartTime:
    def __init__(self, hour: int, minute: int):
        self.hour = hour
        self.minute = minute


class DayOption:
    def __init__(self, dop_name, start_time: StartTime, 
                 program_id: str | None = None, 
                 steps: list[tuple[int,int]] | None = None,
                 day=None):
        
        self.dop_name = dop_name
        self.start_time = start_time
        self.program_id = program_id
        self.steps = steps
        self.day = day  # Day of week (optional)


class Scheduler:
    def __init__(self, logger=None):
        
        self.logger = logger or logging.getLogger(__name__)


        jobstores = {'default': SQLAlchemyJobStore(url='sqlite:///jobs.sqlite')}
        job_defaults = {'coalesce': False, 'max_instances': 10}
        self.scheduler = BackgroundScheduler(jobstores=jobstores, job_defaults=job_defaults, timezone=TZ)  # Create background scheduler

    def _extract_program_id(self, day_opt) -> str:

        if hasattr(day_opt, "program_id") and day_opt.program_id is not None:
            return str(day_opt.program_id)
        if hasattr(day_opt, "steps") and day_opt.steps is not None:
            return ('Adhoc')   # ennél jobb kell  
        raise AttributeError("DayOption needs a program_id or steps defined.")

    # Egyedi, determinisztikus job ID
    def _job_id_for(self, day_opt: "DayOption") -> str:
        pid = self._extract_program_id(day_opt)
        dow = day_opt.day if getattr(day_opt, "day", None) is not None else "daily"
        return f"program:{pid}:{dow}-{day_opt.start_time.hour:02d}{day_opt.start_time.minute:02d}"
   
    def add_day_option(self, dayOption: "DayOption") -> str:
        from jobs import start_program_by_id      # top-level, perzisztens-barát

        pid = self._extract_program_id(dayOption)
        name = getattr(dayOption, "dop_name", f"Program {pid}")

        cron_kwargs = dict(hour=dayOption.start_time.hour, minute=dayOption.start_time.minute)
        if getattr(dayOption, "day", None) is not None:
            cron_kwargs["day_of_week"] = dayOption.day
        
        job_kwargs = {"program_id": pid, "name": name}
        if dayOption.steps:                       # pass steps only when you actually have them
            job_kwargs["steps"] = list(dayOption.steps)
        jid = self._job_id_for(dayOption)

        self.scheduler.add_job(
            start_program_by_id,
            "cron",
            id=jid,
            name=name,
            replace_existing=True,
            kwargs=job_kwargs,
            **cron_kwargs,
        )
        self.logger.debug(
            "Scheduled %s (id=%s) at %s %02d:%02d",
            name, jid, cron_kwargs.get("day_of_week", "every day"),
            dayOption.start_time.hour, dayOption.start_time.minute
        )
        return jid
    
    def remove_day_option(self, dayOption: "DayOption") -> None:
        jid = self._job_id_for(dayOption)
        self.scheduler.remove_job(jid)
        self.logger.debug("Removed job %s", jid)
        
    def adhoc_program_run(self, 
                          steps: list[tuple[int,int]] | None = None,
                          program_id: int | str = "adhoc",
                          name: str = "Adhoc Program") -> str:
        """Run once, almost immediately."""
        from jobs import start_program_by_id
        jid = f"adhoc:{program_id}:{int(datetime.now(TZ).timestamp())}"
        self.scheduler.add_job(
            start_program_by_id,
            'date',
            run_date=datetime.now(TZ) + timedelta(seconds=1),
            id=jid,
            name=name,
            kwargs={'program_id': program_id, 'steps': list(steps), 'name': name},
            replace_existing=False,
        )
        self.logger.debug("Scheduled one-off %s (id=%s) to run now", name, jid)
        return jid
    
    def trigger_now(self, dayOption: "DayOption") -> None:
        """Az adott dayOption-hoz tartozó job azonnali futtatása (következő időpont előrehozása)."""
        jid = self._job_id_for(dayOption)
        self.scheduler.modify_job(jid, next_run_time=datetime.now(TZ))

    def run_program_by_id(self, program_id):  ## ez kell?
        p = program_constructor_from_db(program_id)
        try:
            p.run_sequentially()
        finally:
            p.cleanup()




    
