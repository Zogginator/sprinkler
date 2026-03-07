import threading
import app_runtime
from classes.Program import Program


def start_scheduled_program(program_id: int, rain_skip: bool = False):
    """Called by APScheduler for configured programs. Respects rain-skip."""
    logger = app_runtime.logger
    prog = app_runtime.programs.get(program_id)
    if prog is None:
        logger.error("start_scheduled_program: program %s not found", program_id)
        return
    if rain_skip and app_runtime.rain_sensor and app_runtime.rain_sensor.get_rain_status():
        logger.info("Rain detected — skipping program '%s'", prog["name"])
        return
    steps = [(s["zone_id"], s["minutes"] * 60) for s in prog.get("steps", []) if s["minutes"] > 0]
    if not steps:
        logger.warning("Program '%s' has no runnable steps, skipping", prog["name"])
        return
    start_program_by_id(program_id=program_id, steps=steps, name=prog["name"])


def start_program_by_id(program_id: int | str,
                        steps: list[tuple[int, int]] | None = None,
                        name: str | None = None):
    logger = app_runtime.logger

    if steps is None:
        from classes.Program import program_constructor_from_db
        p = program_constructor_from_db(program_id)
    else:
        logger.debug("start_program_by_id: steps=%r", steps)
        p = Program(program_id, name or f"Program {program_id}", steps, logger=logger)

    stop_event = threading.Event()
    app_runtime.set_current_program(
        name or f"Program {program_id}",
        p.runtimes or [],
        stop_event,
    )

    def _on_step_start():
        app_runtime.advance_current_program_step()

    try:
        p.run_sequentially(on_step_start=_on_step_start, stop_event=stop_event)
    finally:
        app_runtime.clear_current_program()
