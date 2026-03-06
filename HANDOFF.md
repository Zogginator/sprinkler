# Sprinkler Controller — Project Handoff

## Overview

A garden sprinkler controller running on a Raspberry Pi. It controls irrigation zones via MQTT, publishing on/off commands to an OpenBK7231N smart relay device. A Flask web UI lets the user manually trigger zones and view their state. APScheduler (backed by SQLite) provides persistent cron scheduling. The project language mix: Hungarian variable names/comments + English code.

---

## Hardware & Infrastructure

- **Raspberry Pi** — runs the Python backend as a systemd service (`service/sprinkler.service`)
- **OpenBK7231N_08D6ACEA** — smart relay board, controlled via MQTT
- **MQTT broker** — Mosquitto, running in Docker on the same Pi, port 1883
- **Zones** (defined in `zones.yaml`):
  | ID | Name | MQTT Channel |
  |----|------|--------------|
  | 1 | Előkert (Front garden) | 31 |
  | 2 | Oldalkert (Side garden) | 32 |
  | 3 | Hátsókert (Back garden) | 33 |
- **Rain sensor** — channel 10, configured but not yet implemented
- **Failsafe**: max 600 seconds per zone activation

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Web framework | Flask 3.0.3 |
| Frontend | Jinja2 templates + HTMX 1.9.12 (polling every 3s) |
| MQTT client | paho-mqtt 2.1.0 |
| Scheduler | APScheduler 3.10.4 (BackgroundScheduler, SQLite jobstore) |
| Config | `zones.yaml` (YAML) |
| Persistence | `jobs.sqlite` (APScheduler jobs only) |
| Runtime | Python 3.12, systemd service |

---

## Project Structure

```
app.py                  # Flask app + main entry point; starts API thread + scheduler
app_runtime.py          # Shared runtime state: mqttc (OBKMqtt), SPRINKLER_BY_ID dict
jobs.py                 # Top-level job function for APScheduler (must be top-level for pickle)
state.py                # Legacy GlobalState dataclass — pre-OOP era, mostly unused now
zones.yaml              # Hardware config: MQTT broker, zones, failsafe, timezone

classes/
  Sprinkler.py          # Sprinkler, SprinklerRun, RainSensor classes; SPRINKLER_RUN_STATE dict
  Program.py            # Program class: ordered list of (zone_id, seconds) steps
  Scheduler.py          # Scheduler wrapper: APScheduler + DayOption/StartTime value objects

mqtt_client.py          # OBKMqtt: thin paho-mqtt wrapper for the set/get topic scheme
templates/
  base.html             # HTML shell with HTMX CDN
  dashboard.html        # Main page; zones div polls /partial/zones every 3s via HTMX
  _zones_partial.html   # Zone cards partial (on/off forms, remaining time)
static/main.css         # Basic styles
service/sprinkler.service  # systemd unit (waits for MQTT broker before starting)
```

---

## Key Data Flow

1. **Manual zone control**: User submits form -> `POST /zones/<id>/on` -> `Sprinkler.turn_on(seconds)` -> `OBKMqtt.set_channel(channel, 1)` -> MQTT publish to `sprinkler/{channel}/set`. A `threading.Timer` fires `turn_off` after the duration.

2. **Scheduled programs**: `Scheduler.add_day_option(DayOption)` -> APScheduler cron job -> `jobs.start_program_by_id()` -> `Program.run_sequentially()` -> iterates `SprinklerRun` objects, each calls `Sprinkler.turn_on()` and blocks on `run.done` event.

3. **State feedback**: MQTT subscription to `sprinkler/+/get` receives hardware state updates, but currently the `_on_message` callback parses the payload and then does nothing with it (callback reference is commented out).

4. **UI polling**: HTMX polls `/partial/zones` every 3 seconds and replaces the zones div.

---

## Class Relationships

```
Scheduler
  uses --> Program (via jobs.start_program_by_id)
  uses --> DayOption, StartTime (value objects)

Program
  has --> list of (zone_id, seconds) runtimes
  creates --> SprinklerRun objects
  reads --> SPRINKLER_BY_ID from app_runtime

SprinklerRun
  has --> Sprinkler reference
  manages --> countdown timer, done event, state machine

Sprinkler
  has --> OBKMqtt reference
  publishes --> MQTT set commands
```

---

## Known Bugs (Must Fix Before Further Development)

1. **`app_runtime.py:34`** — `conf(["rainsensor"]["channel"])` uses `()` instead of `[]`. Should be `conf["rainsensor"]["channel"]`. This crashes on startup.

2. **`mqtt_client.py:29`** — `self.get_tmpl = get_tmpl` references `get_tmpl` which is not a parameter of `__init__`. Causes `NameError` on instantiation.

3. **`Sprinkler.remaining_time()` (`Sprinkler.py:101`)** — references `self.runs` which does not exist on `Sprinkler`. Runs are tracked externally in `app.py:sprinkler_runs`. This method will always raise `AttributeError`.

4. **`app.py:135` — `api_zones()`** — references undefined name `sprinklers`. Should be `app_runtime.SPRINKLER_BY_ID.values()` or similar.

5. **`_zones_partial.html:3`** — references template variable `G` (GlobalState) which is never passed to the `partial_zones()` Flask route. Will raise `UndefinedError` on every HTMX poll.

6. **`RainSensor.get_rain_status()` (`Sprinkler.py:14`)** — missing `self` parameter in method signature; body is incomplete (no `return`, unclosed `try`).

7. **`Scheduler.py:6`** — timezone hardcoded as `"Europe/Budapest"` instead of reading from config. The config value is available but ignored.

8. **`program_constructor_from_db()` (`Program.py:12`)** — stub returning hardcoded `[(3, 10), (2, 10)]`. Scheduled jobs that load programs by ID from DB will silently run the wrong program.

---

## Incomplete / Stub Features

- **Rain sensor**: Class skeleton exists (`RainSensor`) but `get_rain_status()` is unimplemented. No rain-skip logic wired into the scheduler or program runner.
- **Program persistence**: Programs are defined in code (`app.py:52`). `program_constructor_from_db()` is a placeholder. No database schema or ORM exists for programs.
- **Schedule CRUD API**: Two `POST /api/schedule` endpoints are commented out in `app.py`. No UI for managing schedules.
- **MQTT state feedback**: `_on_message` parses hardware state but the callback is commented out — Sprinkler objects never update their `.state` from real hardware feedback.
- **`get_channel()`** in `OBKMqtt` — is an empty `pass`. No way to query current hardware state on demand.
- **`GlobalState` / `state.py`** — was the original state store before OOP refactor. Now largely orphaned. Still imported in `app.py` but not used functionally.

---

## Suggested Development Priorities

### P0 — Fix crashes (blockers)
- Fix the 5 bugs listed in items 1–5 above so the app starts and the dashboard works.

### P1 — Wire up state feedback
- Implement `OBKMqtt._on_message` callback properly: route received hardware states back to the corresponding `Sprinkler` object (update `.state`).
- Implement `get_channel()` to query hardware state on demand.
- Remove or replace `GlobalState` — the `_zones_partial.html` template should read from `Sprinkler` objects, not `G`.

### P2 — Program & schedule persistence
- Design a simple database schema (SQLite is already in use for jobs): `programs` table with `(id, name)`, `program_steps` table with `(program_id, zone_id, duration_seconds, step_order)`.
- Implement `program_constructor_from_db(program_id)` to actually load from DB.
- Add Flask API endpoints for CRUD on programs and scheduled jobs.
- Add a schedule management page to the web UI.

### P3 — Rain sensor
- Implement `RainSensor.get_rain_status()` via MQTT subscription.
- Add rain-skip logic: before a scheduled program runs, check rain sensor; skip and log if wet.

### P4 — UX improvements
- The web UI is minimal (Hungarian labels, basic forms). Consider:
  - Showing actual remaining time from `SprinklerRun` objects (countdown)
  - Mobile-friendly layout
  - Visual schedule overview (weekly grid)
  - Feedback on successful/failed actions (currently all redirects)

---

## Configuration

All hardware config lives in `zones.yaml`. The path can be overridden via env var `ZONES_CONF`. Sensitive values (MQTT password) are in plaintext — consider moving to env vars or a secrets file for production.

---

## Deployment

Deployed as a systemd service on Raspberry Pi. The service waits up to 120 seconds for the MQTT broker to be available on port 1883 before starting Python. Restart policy: always, 5s delay.

```
sudo systemctl enable sprinkler
sudo systemctl start sprinkler
journalctl -u sprinkler -f
```

Working directory on Pi: `/home/pi/sprinkler`

---

## Git

- Main branch: `master`
- Active dev branch: `aron`
- Recent commit history shows progression: global state -> OOP refactor -> sequential execution -> persistent scheduler with SQLite -> ad-hoc program run support
