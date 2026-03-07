import logging
import os
from threading import Thread
import time

import yaml
from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

import app_runtime
from classes.Scheduler import Scheduler

# ----------------------------
# Config
# ----------------------------
CONF_PATH = os.environ.get("ZONES_CONF", "zones.yaml")
with open(CONF_PATH, "r", encoding="utf-8") as f:
    CONF = yaml.safe_load(f)

_prefix = CONF["mqtt"].get("mqtt_topic_prefix", "sprinkler")
CONF["mqtt"]["topics"] = {
    "set":   f"{_prefix}/{{channel}}/set",
    "get":   f"{_prefix}/{{channel}}/get",
    "state": f"{_prefix}/+/get",
}


ZONES = CONF["zones"]  # [{'id': 1, 'name': 'Előkert', 'channel': 31}, {'id': 2, 'name': 'Oldalkert', 'channel': 32}, {'id': 3, 'name': 'Hátsókert', 'channel': 33}]
ZONES_BY_ID = {z["id"]: z for z in ZONES}  # {1: {'id': 1, 'name': 'Előkert', 'channel': 31}, 2: {'id': 2, 'name': 'Oldalkert', 'channel': 32}, 3: {'id': 3, 'name': 'Hátsókert', 'channel': 33}}
FAILSAFE_MAX = int(CONF.get("failsafe", {}).get("max_seconds", 1800)) # 600
POLL_SEC = int(CONF.get("failsafe", {}).get("poll_seconds", 3))
SET_TMPL = CONF["mqtt"]["topics"]["set"]  # "sprinkler/{channel}/set"
STATE_SUB = CONF["mqtt"]["topics"]["state"] # "sprinkler/+/get"
TIMEZONE = CONF ["timezone"]  #"Europe/Budapest"

app_runtime.init_runtime(CONF)  #mqtttc indítás, és SPRINKLER_BY_ID inicializálás
logging.basicConfig(level=logging.DEBUG)
app_runtime.logger = logging.getLogger(__name__)
                

sched = Scheduler(timezone=TIMEZONE, logger=app_runtime.logger)

# ----------------------------
# Program helpers
# ----------------------------
_DAY_HU = {"mon": "H", "tue": "K", "wed": "Sze", "thu": "Cs", "fri": "P", "sat": "Szo", "sun": "V"}


def _schedule_summary(prog: dict) -> str:
    s = prog.get("schedule", {})
    stype = s.get("type", "daily")
    time_str = s.get("time", "")
    if stype == "daily":
        return f"Naponta {time_str}"
    if stype == "weekly":
        days_hu = ",".join(_DAY_HU.get(d, d) for d in s.get("days", []))
        return f"{days_hu} {time_str}"
    if stype == "once":
        return f"{s.get('date', '')} {time_str} (egyszer)"
    return "–"


def _steps_summary(prog: dict) -> str:
    parts = []
    for step in prog.get("steps", []):
        zone = ZONES_BY_ID.get(step["zone_id"])
        name = zone["name"] if zone else f"Zóna {step['zone_id']}"
        parts.append(f"{name} {step['minutes']}p")
    return " → ".join(parts)


def _register_job(prog: dict) -> None:
    from jobs import start_scheduled_program
    pid = prog["id"]
    job_id = f"program:{pid}"
    try:
        sched.scheduler.remove_job(job_id)
    except Exception:
        pass
    if not prog.get("active", False):
        return
    s = prog.get("schedule", {})
    stype = s.get("type", "daily")
    time_str = s.get("time", "06:00")
    hour, minute = (int(x) for x in time_str.split(":"))
    rain_skip = prog.get("rain_skip", False)
    kw = {"program_id": pid, "rain_skip": rain_skip}
    if stype == "daily":
        sched.scheduler.add_job(
            start_scheduled_program, "cron",
            id=job_id, name=prog["name"], replace_existing=True,
            kwargs=kw, hour=hour, minute=minute,
        )
    elif stype == "weekly":
        days = s.get("days", [])
        if not days:
            return
        sched.scheduler.add_job(
            start_scheduled_program, "cron",
            id=job_id, name=prog["name"], replace_existing=True,
            kwargs=kw, day_of_week=",".join(days), hour=hour, minute=minute,
        )
    elif stype == "once":
        from datetime import datetime
        from zoneinfo import ZoneInfo
        date_str = s.get("date", "")
        if not date_str:
            return
        run_date = datetime.fromisoformat(f"{date_str}T{time_str}:00").replace(
            tzinfo=ZoneInfo(TIMEZONE)
        )
        sched.scheduler.add_job(
            start_scheduled_program, "date",
            id=job_id, name=prog["name"], replace_existing=True,
            kwargs=kw, run_date=run_date,
        )


def _save_conf() -> None:
    """Write config back to zones.yaml, stripping runtime-only keys."""
    mqtt_clean = {k: v for k, v in CONF["mqtt"].items() if k != "topics"}
    conf_to_save = {**CONF, "mqtt": mqtt_clean, "programs": list(app_runtime.programs.values())}
    with open(CONF_PATH, "w", encoding="utf-8") as f:
        yaml.dump(conf_to_save, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


# Load programs from config
for _p in CONF.get("programs", []):
    app_runtime.programs[_p["id"]] = _p
    _register_job(_p)

# ----------------------------
# Flask API
# ----------------------------
app = Flask(__name__)



@app.get("/")
def dashboard():
    jobs_by_id = {j.id: j for j in sched.scheduler.get_jobs()}
    active_programs = []
    for prog in app_runtime.programs.values():
        if not prog.get("active"):
            continue
        job = jobs_by_id.get(f"program:{prog['id']}")
        next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M") if job and job.next_run_time else "–"
        active_programs.append({"name": prog["name"], "next_run": next_run,
                                 "schedule_summary": _schedule_summary(prog)})
    any_zone_on = any(sp.state == 1 for sp in app_runtime.SPRINKLER_BY_ID.values())
    return render_template(
        "dashboard.html",
        zones=ZONES,
        poll_sec=POLL_SEC,
        dry_run=app_runtime.DRY_RUN,
        active_programs=active_programs,
        any_zone_on=any_zone_on,
        last_adhoc_steps=app_runtime.last_adhoc_steps,
    )


@app.get("/partial/zones")
def partial_zones():
    remaining_by_id = {
        zid: app_runtime.remaining(zid)
        for zid in app_runtime.SPRINKLER_BY_ID
    }
    app_runtime.logger.debug("partial_zones remaining_by_id=%r", remaining_by_id)

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

    sprinkler.turn_on(seconds)

    if request.headers.get("HX-Request"):
        return partial_zones()
    return redirect(url_for("dashboard"))


@app.post("/adhoc")
def adhoc_run():
    steps = []
    for z in ZONES:
        key = f"zone_{z['id']}_minutes"
        minutes = int(request.form.get(key) or 0)
        app_runtime.last_adhoc_steps[z["id"]] = minutes
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
                "remaining": app_runtime.remaining(sp.id),
            }
        )
    return jsonify(out)


# ----------------------------
# Programs — JSON API
# ----------------------------

@app.get("/api/programs")
def api_programs_list():
    return jsonify(list(app_runtime.programs.values()))


@app.post("/api/programs")
def api_programs_create():
    data = request.get_json(force=True)
    new_id = max(app_runtime.programs.keys(), default=0) + 1
    data["id"] = new_id
    app_runtime.programs[new_id] = data
    _save_conf()
    _register_job(data)
    return jsonify(data), 201


@app.put("/api/programs/<int:pid>")
def api_programs_update(pid: int):
    if pid not in app_runtime.programs:
        abort(404)
    data = request.get_json(force=True)
    data["id"] = pid
    app_runtime.programs[pid] = data
    _save_conf()
    _register_job(data)
    return jsonify(data)


@app.delete("/api/programs/<int:pid>")
def api_programs_delete(pid: int):
    if pid not in app_runtime.programs:
        abort(404)
    app_runtime.programs.pop(pid)
    _save_conf()
    try:
        sched.scheduler.remove_job(f"program:{pid}")
    except Exception:
        pass
    return "", 204


@app.post("/api/programs/<int:pid>/run")
def api_programs_run(pid: int):
    prog = app_runtime.programs.get(pid)
    if prog is None:
        abort(404)
    steps = [(s["zone_id"], s["minutes"] * 60) for s in prog.get("steps", []) if s["minutes"] > 0]
    if steps:
        sched.adhoc_program_run(steps=steps, name=prog["name"])
    return "", 204


# ----------------------------
# Programs — UI
# ----------------------------

@app.get("/programs")
def programs_page():
    jobs_by_id = {j.id: j for j in sched.scheduler.get_jobs()}
    programs_view = []
    for prog in app_runtime.programs.values():
        job = jobs_by_id.get(f"program:{prog['id']}")
        next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M") if job and job.next_run_time else "–"
        programs_view.append({
            **prog,
            "next_run": next_run,
            "schedule_summary": _schedule_summary(prog),
            "steps_summary": _steps_summary(prog),
        })
    return render_template("programs.html", programs=programs_view, zones=ZONES,
                           dry_run=app_runtime.DRY_RUN)


@app.get("/programs/new")
def program_new():
    return render_template("program_form.html", program=None, zones=ZONES,
                           failsafe_max=FAILSAFE_MAX, dry_run=app_runtime.DRY_RUN)


@app.get("/programs/<int:pid>/edit")
def program_edit(pid: int):
    prog = app_runtime.programs.get(pid)
    if prog is None:
        abort(404)
    return render_template("program_form.html", program=prog, zones=ZONES,
                           failsafe_max=FAILSAFE_MAX, dry_run=app_runtime.DRY_RUN)


@app.post("/programs/save")
def program_save():
    pid_str = request.form.get("id")
    steps = []
    for z in ZONES:
        minutes = int(request.form.get(f"zone_{z['id']}_minutes") or 0)
        if minutes > 0:
            steps.append({"zone_id": z["id"], "minutes": minutes})
    prog = {
        "name": request.form.get("name", "").strip(),
        "active": request.form.get("active") == "1",
        "rain_skip": request.form.get("rain_skip") == "1",
        "schedule": {
            "type": request.form.get("schedule_type", "daily"),
            "time": request.form.get("schedule_time", "06:00"),
            "days": request.form.getlist("schedule_days"),
            "date": request.form.get("schedule_date", ""),
        },
        "steps": steps,
    }
    if pid_str:
        pid = int(pid_str)
    else:
        pid = max(app_runtime.programs.keys(), default=0) + 1
    prog["id"] = pid
    app_runtime.programs[pid] = prog
    _save_conf()
    _register_job(prog)
    return redirect(url_for("programs_page"))


@app.post("/programs/<int:pid>/delete")
def program_delete(pid: int):
    if pid not in app_runtime.programs:
        abort(404)
    app_runtime.programs.pop(pid)
    _save_conf()
    try:
        sched.scheduler.remove_job(f"program:{pid}")
    except Exception:
        pass
    return redirect(url_for("programs_page"))


@app.post("/programs/<int:pid>/run")
def program_run(pid: int):
    prog = app_runtime.programs.get(pid)
    if prog is None:
        abort(404)
    steps = [(s["zone_id"], s["minutes"] * 60) for s in prog.get("steps", []) if s["minutes"] > 0]
    if steps:
        sched.adhoc_program_run(steps=steps, name=prog["name"])
    return redirect(url_for("programs_page"))


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
