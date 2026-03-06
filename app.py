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
from classes.Sprinkler import Sprinkler, SprinklerRun, RainSensor
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

program1 = program_constructor('1', 'Temporary Program', [(3, 10), (2,10)])     # <classes.Program.Program object>
test2 = DayOption("Everyday Evening", StartTime(21, 14), program_id='test', steps=[(3,600), (2, 600), (1,600)], day=None)    # <classes.Scheduler.DayOption object>


sched = Scheduler(logger=app_runtime.logger)            #<classes.Scheduler.Scheduler object>


# ----------------------------
# Flask API
# ----------------------------
app = Flask(__name__)



@app.get("/")
def dashboard():
    jobs = sched.scheduler.get_jobs()
    scheduled_jobs = [
        {
            "name": j.name,
            "next_run": j.next_run_time.strftime("%Y-%m-%d %H:%M") if j.next_run_time else "–",
        }
        for j in jobs
    ]
    any_zone_on = any(sp.state == 1 for sp in app_runtime.SPRINKLER_BY_ID.values())
    return render_template(
        "dashboard.html",
        zones=ZONES,
        poll_sec=POLL_SEC,
        dry_run=app_runtime.DRY_RUN,
        scheduled_jobs=scheduled_jobs,
        any_zone_on=any_zone_on,
    )


@app.get("/partial/zones")
def partial_zones():
    remaining_by_id = {}
    for run in sprinkler_runs:
        sid = run.sprinkler.id
        if getattr(run, "_active", False) and sid not in remaining_by_id:
            remaining_by_id[sid] = max(0, int(run.remaining_time))
    return render_template(
        "_zones_partial.html",
        zones=ZONES,
        sprinklers=app_runtime.SPRINKLER_BY_ID,
        remaining_by_id=remaining_by_id,
        poll_sec=POLL_SEC,
        failsafe_max=FAILSAFE_MAX,
    )


@app.post("/zones/<int:zid>/on")
def zone_on(zid: int):
    minutes = request.form.get("minutes")
    if minutes:
        seconds = int(minutes) * 60
    else:
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

    sprinkler = app_runtime.SPRINKLER_BY_ID.get(zid)
    if sprinkler is None:
        abort(404)

    run = sprinkler.turn_on(seconds)
    if run:
        sprinkler_runs.append(run)
        def _cleanup(r):
            r.done.wait()
            try:
                sprinkler_runs.remove(r)
            except ValueError:
                pass
        Thread(target=_cleanup, args=(run,), daemon=True).start()

    return redirect(url_for("dashboard"))


@app.post("/adhoc")
def adhoc_run():
    steps = []
    for z in ZONES:
        key = f"zone_{z['id']}_minutes"
        minutes = int(request.form.get(key) or 0)
        if minutes > 0:
            steps.append((z["id"], min(minutes * 60, FAILSAFE_MAX)))
    if steps:
        sched.adhoc_program_run(steps=steps, name="Azonnali program")
    return redirect(url_for("dashboard"))


@app.post("/zones/<int:zid>/off")
def zone_off(zid: int):
    # z = ZONES_BY_ID.get(zid) or abort(404)
    # mqttc.set_channel(z["channel"], 0)
    # G.set_on(zid, False, remaining=None)

    sprinkler = app_runtime.SPRINKLER_BY_ID.get(zid)
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
    for sp in app_runtime.SPRINKLER_BY_ID.values():
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
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sched.scheduler.shutdown()
