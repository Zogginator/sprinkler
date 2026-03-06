import logging
from mqtt_client import OBKMqtt
from classes.Sprinkler import Sprinkler, RainSensor

mqttc: OBKMqtt | None = None  # ide kerül az OBKMqtt példány induláskor
logger = logging.getLogger("sprinkler")  # központi logger
SPRINKLER_BY_ID: dict[int, Sprinkler] = {}
DRY_RUN: bool = False

def init_runtime(conf):
    global mqttc, SPRINKLER_BY_ID, DRY_RUN
    DRY_RUN = bool(conf.get("dry_run", False))

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
        sp = _sprinkler_by_channel.get(channel)
        if sp is not None:
            sp.state = value
            logger.debug("State update from hardware: channel=%d state=%d", channel, value)
        else:
            logger.warning("Received state for unknown channel %d", channel)

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

    rainsensor = RainSensor (mqttc = mqttc, channel = conf["rainsensor"]["channel"])