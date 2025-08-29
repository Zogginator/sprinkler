import app_runtime
from classes.Program import Program


def start_program_by_id(program_id: int | str, 
                        steps: list[tuple[int,int]] | None = None, 
                        name: str | None = None):
    spr_by_id = app_runtime.SPRINKLER_BY_ID   # ← innen vesszük, NEM kwargs-ból adjuk át
    logger= app_runtime.logger

    #steps = [(3, 10), (2,10)]   # adatbázisból kell majd felhúzni

    if steps is None:
        from classes.Program import program_constructor_from_db
        p = program_constructor_from_db(program_id)
    else:
        logger.debug("start_program_by_id_job: steps=%r", steps)
        p = Program(program_id, name or f"Program {program_id}", steps, spr_by_id, logger=logger)
    try:
        p.run_sequentially()
    finally:
        p.cleanup()
    