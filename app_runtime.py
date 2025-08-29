import logging
from mqtt_client import OBKMqtt
from classes.Sprinkler import Sprinkler

mqttc: OBKMqtt | None = None  # ide kerül az OBKMqtt példány induláskor
logger = logging.getLogger("sprinkler")  # központi logger
SPRINKLER_BY_ID: dict[int, Sprinkler] = {}

def init_runtime(conf):
    global mqttc, SPRINKLER_BY_ID
    mqttc = OBKMqtt(
        host=conf["mqtt"]["host"], # '192.168.1.173'
        port=int(conf["mqtt"]["port"]), # 1883
        username=conf["mqtt"].get("username",""), # 'homeassistant'
        password=conf["mqtt"].get("password",""), # 'nadap'
        qos=int(conf["mqtt"].get("qos",1)), # 1
        set_tmpl=conf["mqtt"]["topics"]["set"], # 'sprinkler/{channel}/set'
        state_sub=conf["mqtt"]["topics"]["state"], # 'sprinkler/+/get'
    )
    mqttc.start()

    SPRINKLER_BY_ID = {  # {1: <Sprinkler object 1>, 2: <Sprinkler object 1>, 3: <Sprinkler object 1>}
        z["id"]: Sprinkler(
            mqttc=mqttc,
            zones_by_id=None,
            name=z["name"],
            channel=z["channel"],
            id=z["id"],
            logger=logger,
        )
        for z in conf["zones"]
    }