import app_runtime
from classes.Program import Program


def start_program_by_id(program_id: int | str,
                        steps: list[tuple[int,int]] | None = None,
                        name: str | None = None):
    spr_by_id = app_runtime.SPRINKLER_BY_ID
    logger = app_runtime.logger

    if steps is None:
        from classes.Program import program_constructor_from_db
        p = program_constructor_from_db(program_id)
    else:
        logger.debug("start_program_by_id_job: steps=%r", steps)
        p = Program(program_id, name or f"Program {program_id}", steps, spr_by_id, logger=logger)

    app_runtime.set_current_program(name or f"Program {program_id}", p.runtimes or [])

    def _on_run_start(r):
        app_runtime.register_run(r)
        app_runtime.advance_current_program_step()

    try:
        p.run_sequentially(on_run_start=_on_run_start)
    finally:
        p.cleanup()
        app_runtime.clear_current_program()
