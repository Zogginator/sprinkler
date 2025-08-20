import os
import yaml
from threading import Timer
from flask import Flask, render_template, request, redirect, url_for, jsonify, abort

from state import GlobalState
from mqtt_client import OBKMqtt  # a módosított, channel/set-get sémát használó kliens

# ----------------------------
# 1) Konfiguráció betöltése
# ----------------------------
CONF_PATH = os.environ.get("ZONES_CONF", "zones.yaml")
with open(CONF_PATH, "r", encoding="utf-8") as f:
    CONF = yaml.safe_load(f)

ZONES = CONF["zones"]
ZONES_BY_ID = {z["id"]: z for z in ZONES}
FAILSAFE_MAX = int(CONF.get("failsafe", {}).get("max_seconds", 1800))
POLL_SEC = int(CONF.get("poll_seconds", 3))

SET_TMPL = CONF["mqtt"]["topics"]["set"]     # pl. "sprinkler/{channel}/set"
STATE_SUB = CONF["mqtt"]["topics"]["state"]  # pl. "sprinkler/+/get"

# csatorna -> zóna id gyors lookup
CHAN_TO_ZONEID = {z["channel"]: z["id"] for z in ZONES}

# ----------------------------
# 2) Globális állapot
# ----------------------------
G = GlobalState()
for z in ZONES:
    G.ensure_zone(z["id"])

def on_state_channel(channel: int, value: int):
    """OBK állapot callback: sprinkler/<ch>/get → '1'/'0'"""
    zid = CHAN_TO_ZONEID.get(channel)
    if zid is not None:
        # a remaining értéket meghagyjuk, csak az ON/KI állapotot frissítjük
        G.set_on(zid, bool(value), G.zones[zid].remaining)

# ----------------------------
# 3) MQTT kliens indítása
# ----------------------------
mqttc = OBKMqtt(
    host=CONF["mqtt"]["host"],
    port=int(CONF["mqtt"]["port"]),
    username=CONF["mqtt"].get("username", ""),
    password=CONF["mqtt"].get("password", ""),
    qos=int(CONF["mqtt"].get("qos", 1)),
    set_tmpl=SET_TMPL,
    state_sub=STATE_SUB,
    on_state_cb=on_state_channel,
)
mqttc.start()

# ----------------------------
# 4) Flask alkalmazás
# ----------------------------
app = Flask(__name__)

@app.get("/")
def dashboard():
    # a zones blokkot HTMX tölti majd be és frissíti
    return render_template("dashboard.html", zones=ZONES, poll_sec=POLL_SEC)

@app.get("/partial/zones")
def partial_zones():
    # csak a zónák rész-sablont rendereljük (HTMX)
    return render_template("_zones_partial.html", zones=ZONES, G=G)

@app.post("/zones/<int:zid>/on")
def zone_on(zid: int):
    seconds = int(request.form.get("seconds") or request.args.get("seconds") or 60)
    if seconds > FAILSAFE_MAX:
        seconds = FAILSAFE_MAX
    z = ZONES_BY_ID.get(zid) or abort(404)

    # 1) MQTT publish: sprinkler/<ch>/set  "1"
    mqttc.set_channel(z["channel"], 1)

    # 2) helyi állapot beállítás + visszaszámláló
    G.set_on(zid, True, remaining=seconds)

    # 3) időzített kikapcsolás (egyszerű Timer)
    def _off():
        mqttc.set_channel(z["channel"], 0)
        G.set_on(zid, False, remaining=None)

    Timer(seconds, _off).start()

    return redirect(url_for("dashboard"))

@app.post("/zones/<int:zid>/off")
def zone_off(zid: int):
    z = ZONES_BY_ID.get(zid) or abort(404)
    mqttc.set_channel(z["channel"], 0)
    G.set_on(zid, False, remaining=None)
    return redirect(url_for("dashboard"))

# Opcionális: egyszerű JSON API
@app.get("/api/zones")
def api_zones():
    out = []
    for z in ZONES:
        st = G.zones[z["id"]]
        out.append({
            "id": z["id"],
            "name": z["name"],
            "channel": z["channel"],
            "on": st.on,
            "remaining": st.remaining
        })
    return jsonify(out)

if __name__ == "__main__":
    # Debug nélkül fut Pi1-en
    app.run(host="0.0.0.0", port=5000)
