import threading
import app_runtime
from classes.Program import Program


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
