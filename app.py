import logging
import os
from threading import Thread
import time

import yaml
from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

import app_runtime
from classes.Program import Program, program_constructor
from classes.Scheduler import DayOption, Scheduler, StartTime

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
    for run in app_runtime.sprinkler_runs:
        sid = run.sprinkler.id
        if getattr(run, "_active", False) and sid not in remaining_by_id:
            remaining_by_id[sid] = max(0, int(run.remaining_time))

    any_zone_on = any(sp.state == 1 for sp in app_runtime.SPRINKLER_BY_ID.values())

    cp = app_runtime.current_program
    program_zone_id = None
    if cp and cp["current_step"] > 0:
        idx = cp["current_step"] - 1
        if 0 <= idx < len(cp["steps"]):
            program_zone_id = cp["steps"][idx][0]

    return render_template(
        "_zones_partial.html",
        zones=ZONES,
        sprinklers=app_runtime.SPRINKLER_BY_ID,
        remaining_by_id=remaining_by_id,
        poll_sec=POLL_SEC,
        failsafe_max=FAILSAFE_MAX,
        any_zone_on=any_zone_on,
        current_program=cp,
        program_zone_id=program_zone_id,
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

    sprinkler = app_runtime.SPRINKLER_BY_ID.get(zid)
    if sprinkler is None:
        abort(404)

    # If a program is running and this zone is not its current step, abort the program
    if app_runtime.current_program and zid != app_runtime._current_program_zone_id():
        app_runtime.abort_current_program()

    run = sprinkler.turn_on(seconds)
    if run is not None and hasattr(run, 'sprinkler'):
        app_runtime.register_run(run)

    if request.headers.get("HX-Request"):
        return partial_zones()
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
    sprinkler = app_runtime.SPRINKLER_BY_ID.get(zid)
    if sprinkler is None:
        abort(404)
    sprinkler.turn_off()
    if request.headers.get("HX-Request"):
        return partial_zones()
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
