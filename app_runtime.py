import logging
import time
import threading
from threading import Event

from mqtt_client import OBKMqtt
from classes.Sprinkler import Sprinkler, RainSensor

mqttc: OBKMqtt | None = None
rain_sensor: RainSensor | None = None
logger = logging.getLogger("sprinkler")
SPRINKLER_BY_ID: dict[int, Sprinkler] = {}
DRY_RUN: bool = False
FAILSAFE_MAX: int = 600

# Single source of truth for run timing: zone_id -> {"started_at": float, "duration": int}
active_runs: dict[int, dict] = {}

current_program: dict | None = None
_program_stop_event: Event | None = None


def start_run(zone_id: int, duration_seconds: int) -> None:
    active_runs[zone_id] = {
        "started_at": time.time(),
        "duration": duration_seconds,
    }


def stop_run(zone_id: int) -> None:
    active_runs.pop(zone_id, None)


def remaining(zone_id: int) -> int:
    run = active_runs.get(zone_id)
    if not run:
        return 0
    r = run["duration"] - (time.time() - run["started_at"])
    return max(0, int(r))


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
    global _program_stop_event
    if _program_stop_event is not None:
        _program_stop_event.set()
    for zone_id in list(active_runs):
        sp = SPRINKLER_BY_ID.get(zone_id)
        if sp:
            sp.turn_off()
    clear_current_program()


def clear_current_program() -> None:
    global current_program, _program_stop_event
    current_program = None
    _program_stop_event = None


def _current_program_zone_id() -> int | None:
    if current_program and current_program["current_step"] > 0:
        idx = current_program["current_step"] - 1
        if 0 <= idx < len(current_program["steps"]):
            return current_program["steps"][idx][0]
    return None


def _failsafe_loop() -> None:
    while True:
        time.sleep(1)
        for zone_id in list(active_runs):
            if remaining(zone_id) == 0:
                sp = SPRINKLER_BY_ID.get(zone_id)
                if sp:
                    logger.info("Failsafe: turning off zone %d", zone_id)
                    sp.turn_off()


def init_runtime(conf):
    global mqttc, SPRINKLER_BY_ID, DRY_RUN, FAILSAFE_MAX
    DRY_RUN = bool(conf.get("dry_run", False))
    FAILSAFE_MAX = int(conf.get("failsafe", {}).get("max_seconds", 600))

    SPRINKLER_BY_ID = {
        z["id"]: Sprinkler(
            id=z["id"],
            name=z["name"],
            channel=z["channel"],
            mqttc=None,  # set after mqttc is created
            logger=logger,
        )
        for z in conf["zones"]
    }

    _sprinkler_by_channel = {sp.channel: sp for sp in SPRINKLER_BY_ID.values()}

    def _on_state(channel: int, value: int):
        sp = _sprinkler_by_channel.get(channel)
        if sp is None:
            logger.warning("Received state for unknown channel %d", channel)
            return
        sp.state = value
        logger.debug("State update: channel=%d state=%d", channel, value)
        if value == 0:
            stop_run(sp.id)
        else:
            if current_program and sp.id != _current_program_zone_id():
                logger.info(
                    "External ON on channel %d conflicts with program — aborting", channel
                )
                abort_current_program()
            if sp.id not in active_runs:
                logger.info(
                    "External ON on channel %d — creating failsafe run (%ds)", channel, FAILSAFE_MAX
                )
                start_run(sp.id, FAILSAFE_MAX)

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

    threading.Thread(target=_failsafe_loop, daemon=True).start()

    global rain_sensor
    rain_sensor = RainSensor(mqttc=mqttc, channel=conf["rainsensor"]["channel"])
