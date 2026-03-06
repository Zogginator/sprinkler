import logging
from threading import Thread, Event
from mqtt_client import OBKMqtt
from classes.Sprinkler import Sprinkler, RainSensor

mqttc: OBKMqtt | None = None  # ide kerül az OBKMqtt példány induláskor
logger = logging.getLogger("sprinkler")  # központi logger
SPRINKLER_BY_ID: dict[int, Sprinkler] = {}
DRY_RUN: bool = False
FAILSAFE_MAX: int = 600
sprinkler_runs: list = []  # all active SprinklerRun objects (manual + program-started)
current_program: dict | None = None  # {"name", "steps", "current_step", "total_steps"}
_program_stop_event: Event | None = None  # signals run_sequentially to stop iterating


def register_run(run) -> None:
    """Append run to sprinkler_runs and start a daemon that removes it when done."""
    sprinkler_runs.append(run)
    def _cleanup(r):
        r.done.wait()
        try:
            sprinkler_runs.remove(r)
        except ValueError:
            pass
    Thread(target=_cleanup, args=(run,), daemon=True).start()


def set_current_program(name: str, steps: list, stop_event: Event) -> None:
    global current_program, _program_stop_event
    _program_stop_event = stop_event
    current_program = {
        "name": name,
        "steps": list(steps),
        "current_step": 0,
        "total_steps": len(steps),
    }


def advance_current_program_step() -> None:
    global current_program
    if current_program is not None:
        current_program["current_step"] += 1


def abort_current_program() -> None:
    """Stop a running program immediately: signal the loop, stop active runs, clear state."""
    global _program_stop_event
    if _program_stop_event is not None:
        _program_stop_event.set()
    if current_program:
        program_zone_ids = {step[0] for step in current_program["steps"]}
        for run in list(sprinkler_runs):
            if getattr(run, "_active", False) and run.sprinkler.id in program_zone_ids:
                run.stop()
    clear_current_program()


def clear_current_program() -> None:
    global current_program, _program_stop_event
    current_program = None
    _program_stop_event = None


def _current_program_zone_id() -> int | None:
    """Return the zone id currently active in the running program, or None."""
    if current_program and current_program["current_step"] > 0:
        idx = current_program["current_step"] - 1
        if 0 <= idx < len(current_program["steps"]):
            return current_program["steps"][idx][0]
    return None


def init_runtime(conf):
    global mqttc, SPRINKLER_BY_ID, DRY_RUN, FAILSAFE_MAX
    DRY_RUN = bool(conf.get("dry_run", False))
    FAILSAFE_MAX = int(conf.get("failsafe", {}).get("max_seconds", 600))

    SPRINKLER_BY_ID = {
        z["id"]: Sprinkler(
            mqttc=None,  # set after mqttc is created
            zones_by_id=None,
            name=z["name"],
            channel=z["channel"],
            id=z["id"],
            logger=logger,
        )
        for z in conf["zones"]
    }

    # channel → sprinkler lookup for the MQTT callback
    _sprinkler_by_channel = {sp.channel: sp for sp in SPRINKLER_BY_ID.values()}

    def _on_state(channel: int, value: int):
        from classes.Sprinkler import SprinklerRun
        sp = _sprinkler_by_channel.get(channel)
        if sp is None:
            logger.warning("Received state for unknown channel %d", channel)
            return
        sp.state = value
        logger.debug("State update from hardware: channel=%d state=%d", channel, value)
        if value == 0:
            # Hardware reported OFF — stop any active run so it doesn't desync
            for run in list(sprinkler_runs):
                if run.sprinkler is sp and getattr(run, "_active", False):
                    logger.info(
                        "Hardware OFF on channel %d — terminating active run", channel
                    )
                    run.stop()
        else:
            # Hardware reported ON for a different zone than the current program step
            # → abort the program so it doesn't fight the externally triggered zone
            if current_program and sp.id != _current_program_zone_id():
                logger.info(
                    "External ON on channel %d conflicts with running program — aborting program",
                    channel,
                )
                abort_current_program()
            # Create a failsafe run if no active run exists for this sprinkler
            has_active_run = any(
                r.sprinkler is sp and getattr(r, "_active", False)
                for r in sprinkler_runs
            )
            if not has_active_run:
                logger.info(
                    "External ON on channel %d — creating failsafe SprinklerRun (%ds)",
                    channel, FAILSAFE_MAX,
                )
                run = SprinklerRun(run_time=FAILSAFE_MAX, sprinkler=sp, logger=logger)
                run.run()
                register_run(run)

    mqttc = OBKMqtt(
        host=conf["mqtt"]["host"],
        port=int(conf["mqtt"]["port"]),
        username=conf["mqtt"].get("username", ""),
        password=conf["mqtt"].get("password", ""),
        qos=int(conf["mqtt"].get("qos", 1)),
        set_tmpl=conf["mqtt"]["topics"]["set"],
        state_sub=conf["mqtt"]["topics"]["state"],
        on_state_cb=_on_state,
        dry_run=DRY_RUN,
    )

    for sp in SPRINKLER_BY_ID.values():
        sp.mqttc = mqttc

    mqttc.start()

    rainsensor = RainSensor(mqttc=mqttc, channel=conf["rainsensor"]["channel"])
