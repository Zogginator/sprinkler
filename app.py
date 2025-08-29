import logging
import os
from threading import Thread, Timer
import time
from datetime import datetime

import yaml
from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

import app_runtime 
from classes.Program import Program, program_constructor
from classes.Scheduler import DayOption, Scheduler, StartTime
from classes.Sprinkler import Sprinkler, SprinklerRun
from mqtt_client import OBKMqtt  # a módosított, channel/set-get sémát használó kliens
from state import GlobalState

# ----------------------------
# Config
# ----------------------------
CONF_PATH = os.environ.get("ZONES_CONF", "zones.yaml")
with open(CONF_PATH, "r", encoding="utf-8") as f:
    CONF = yaml.safe_load(f)


ZONES = CONF["zones"]  # [{'id': 1, 'name': 'Előkert', 'channel': 31}, {'id': 2, 'name': 'Oldalkert', 'channel': 32}, {'id': 3, 'name': 'Hátsókert', 'channel': 33}]
ZONES_BY_ID = {z["id"]: z for z in ZONES}  # {1: {'id': 1, 'name': 'Előkert', 'channel': 31}, 2: {'id': 2, 'name': 'Oldalkert', 'channel': 32}, 3: {'id': 3, 'name': 'Hátsókert', 'channel': 33}}
FAILSAFE_MAX = int(CONF.get("failsafe", {}).get("max_seconds", 1800)) # 600
POLL_SEC = int(CONF.get("poll_seconds", 3)) #3
SET_TMPL = CONF["mqtt"]["topics"]["set"]  # "sprinkler/{channel}/set"
STATE_SUB = CONF["mqtt"]["topics"]["state"] # "sprinkler/+/get"
TIMEZONE = CONF ["timezone"]  #"Europe/Budapest"

app_runtime.init_runtime(CONF)  #mqtttc indítás, és SPRINKLER_BY_ID inicializálás
logging.basicConfig(level=logging.DEBUG)
app_runtime.logger = logging.getLogger(__name__)
                

sprinkler_runs = []

def get_remaining_total_runtime():
    return sum(run.remaining_time for run in sprinkler_runs)

def get_remaining_runtimes_for_sprinkler(sprinkler_id):
    runs = [run for run in sprinkler_runs if run.sprinkler.id == sprinkler_id]
    if len(runs) > 1:
        return [run.remaining_time for run in runs]
    elif len(runs) == 1:
        return runs[0].remaining_time
    else:
        return

program1 = program_constructor('1', 'Temporary Program', [(3, 10), (2,10)])     # <classes.Program.Program object>
test2 = DayOption("Everyday Evening", StartTime(21, 14), program_id='test', steps=[(3,600), (2, 600), (1,600)], day=None)    # <classes.Scheduler.DayOption object>


sched = Scheduler(logger=app_runtime.logger)            #<classes.Scheduler.Scheduler object>


# ----------------------------
# Flask API
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
    # z = ZONES_BY_ID.get(zid) or abort(404)

    # # 1) MQTT publish: sprinkler/<ch>/set  "1"
    # mqttc.set_channel(z["channel"], 1)

    # # 2) helyi állapot beállítás + visszaszámláló
    # G.set_on(zid, True, remaining=seconds)

    # # 3) időzített kikapcsolás (egyszerű Timer)
    # def _off():
    #     mqttc.set_channel(z["channel"], 0)
    #     G.set_on(zid, False, remaining=None)

    # Timer(seconds, _off).start()

    sprinkler = SPRINKLER_BY_ID.get(zid)
    if sprinkler is None:
        abort(404)

    run = sprinkler.turn_on(seconds)
    if run:
        sprinkler_runs.append(run)

    return redirect(url_for("dashboard"))


@app.post("/zones/<int:zid>/off")
def zone_off(zid: int):
    # z = ZONES_BY_ID.get(zid) or abort(404)
    # mqttc.set_channel(z["channel"], 0)
    # G.set_on(zid, False, remaining=None)

    sprinkler = SPRINKLER_BY_ID.get(zid)
    if sprinkler is None:
        abort(404)
    sprinkler.turn_off()
    return redirect(url_for("dashboard"))


# Opcionális: egyszerű JSON API
@app.get("/api/zones")
def api_zones():
    out = []
    # for z in ZONES:
    #     st = G.zones[z["id"]]
    #     out.append({
    #         "id": z["id"],
    #         "name": z["name"],
    #         "channel": z["channel"],
    #         "on": st.on,
    #         "remaining": st.remaining
    #     })
    for sp in sprinklers:
        out.append(
            {
                "id": sp.id,
                "name": sp.name,
                "channel": sp.channel,
                "on": sp.state == 1,
                "remaining": sp.remaining_time(),
            }
        )
    return jsonify(out)


# @app.post("api/schedule")
# def update_scheduler():

# @app.post("api/schedule")
# def update_scheduler():


# ----------------------------
# Main
# ----------------------------
def run_app():
    app.run(host="0.0.0.0", port=5000)


# def run_scheduler():
#   sched.scheduler.start()


api_thread = Thread(target=run_app, daemon=True)


if __name__ == "__main__":
    
    api_thread.start()
    sched.scheduler.start()

