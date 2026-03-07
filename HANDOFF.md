# Sprinkler Controller — Project Handoff

## Overview

A garden sprinkler controller running on a Raspberry Pi. It controls irrigation zones via MQTT, publishing on/off commands to an OpenBK7231N smart relay device. A Flask web UI lets the user manually trigger zones, run ad-hoc programs, create and schedule named programs, and view live zone state. APScheduler (in-memory) provides cron/date scheduling. Programs and their schedules are persisted in `zones.yaml`. The project language mix: Hungarian variable names/UI labels + English code.

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
- **Rain sensor** — channel 10, object instantiated, `get_rain_status()` stub returns `False`
- **Failsafe**: max 600 seconds per zone activation (configurable via `failsafe.max_seconds`)

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Web framework | Flask 3.0+ |
| Frontend | Jinja2 templates + HTMX 1.9.12 (polling every 3s) |
| MQTT client | paho-mqtt 2.1.0 |
| Scheduler | APScheduler 3.10.4 (BackgroundScheduler, in-memory jobstore) |
| Config | `zones.yaml` (YAML, gitignored) |
| Runtime | Python 3.12, systemd service |

---

## Project Structure

```
app.py                  # Flask app + main entry point; config, helpers, all routes
app_runtime.py          # Shared runtime state: MQTT client, sprinklers, timing, programs
jobs.py                 # APScheduler job functions (must be importable at top level)
zones.yaml              # Hardware + program config — GITIGNORED, create from example
zones.yaml.example      # Template config (committed, no secrets)

classes/
  Sprinkler.py          # Sprinkler, RainSensor — MQTT control + state
  Program.py            # Program class: sequential zone execution
  Scheduler.py          # APScheduler wrapper + DayOption/StartTime value objects

mqtt_client.py          # OBKMqtt: paho-mqtt wrapper for the set/get topic scheme
mock_openbk.py          # Standalone MQTT relay simulator for hardware-free testing
deploy.sh               # Pi deploy: git pull + systemctl restart + journal tail
requirements.txt        # Python dependencies
service/sprinkler.service  # systemd unit (waits for MQTT broker before starting)

templates/
  base.html               # HTML shell; input preservation, countdown JS, htmx:afterRequest
  dashboard.html          # Main page: zone grid + ad-hoc form + programs section
  _zones_partial.html     # Zone cards partial (BE/KI forms, live countdown, program status)
  _programs_partial.html  # Programs section partial (full CRUD, inline forms, HTMX-driven)
static/main.css           # Mobile-first CSS (1-col → 3-col grid)
```

---

## Configuration (`zones.yaml`)

`zones.yaml` is gitignored. Copy `zones.yaml.example` and fill in real values on first deploy.

Key fields:
```yaml
mqtt:
  host: 192.168.x.x
  port: 1883
  username: "..."
  password: "..."
  mqtt_topic_prefix: "sprinkler"   # change to "sprinkler_test" for local testing

rainsensor:
  channel: 10

failsafe:
  max_seconds: 600
  poll_seconds: 3

timezone: "Europe/Budapest"
dry_run: false

programs:
  - id: 1
    name: "Reggeli öntözés"
    active: true
    rain_skip: true
    schedule:
      type: "daily"       # "daily", "weekly", "once"
      time: "06:00"
      days: []            # for weekly: [mon, tue, wed, thu, fri, sat, sun]
      date: ""            # for once: "2026-04-01"
    steps:
      - zone_id: 1
        minutes: 10
      - zone_id: 2
        minutes: 8
      - zone_id: 3
        minutes: 12
```

`programs` is the source of truth for scheduled programs. On startup, `app.py` reads this list, populates `app_runtime.programs`, and registers APScheduler jobs. On any program create/update/delete, `_save_conf()` writes the updated list back to `zones.yaml` (stripping runtime-only keys like `mqtt.topics`).

---

## State Model

Everything flows through these sources of truth:

1. **`sp.state`** (`Sprinkler` object) — hardware ON/OFF state. Set optimistically on `turn_on()`/`turn_off()`, confirmed by MQTT feedback via `_on_state()`.

2. **`app_runtime.active_runs`** — `dict[zone_id, {started_at: float, duration: int}]`. Tracks when each zone started and for how long. `remaining(zone_id)` computes seconds left. `_failsafe_loop` auto-calls `turn_off()` when remaining hits zero.

3. **`app_runtime.programs`** — `dict[int, dict]`. All named programs keyed by ID. Loaded from `zones.yaml` at startup, updated by UI/API operations, written back on every change.

4. **`app_runtime.current_program`** — `dict | None`. Set when a program is running (`name`, `steps`, `current_step`, `total_steps`). Cleared when it finishes or is aborted.

5. **`app_runtime.last_adhoc_steps`** — `dict[int, int]` (zone_id → minutes). Persists the last ad-hoc form submission so the form pre-fills on reload. Defaults to 5 minutes per zone on first load.

6. **`app_runtime.rain_sensor`** — module-level `RainSensor` instance (not GC'd). `get_rain_status()` currently returns `False` (stub).

There are no `SprinklerRun` objects, no threading timers, no cleanup daemons.

---

## Key Data Flow

### Manual zone on/off
```
POST /zones/<id>/on
  → Sprinkler.turn_on(seconds)
      → sp.state = 1 (optimistic)
      → OBKMqtt.set_channel(channel, 1)
      → app_runtime.start_run(id, seconds)
  → partial_zones() returned (HTMX swaps zone grid)
  → htmx:afterRequest triggers immediate zones poll
```

```
POST /zones/<id>/off
  → Sprinkler.turn_off()
      → sp.state = 0
      → app_runtime.stop_run(id)
      → OBKMqtt.set_channel(channel, 0)
  → partial_zones() returned
  → htmx:afterRequest triggers immediate zones poll
```

### Failsafe auto-off
```
_failsafe_loop (daemon thread, 1s tick)
  → for each active_run: if remaining(zone_id) == 0: sp.turn_off()
```

### Scheduled programs
```
APScheduler cron/date → jobs.start_scheduled_program(program_id, rain_skip)
  → load prog from app_runtime.programs
  → if rain_skip and rain_sensor.get_rain_status(): log + return
  → start_program_by_id(program_id, steps, name)
      → Program.run_sequentially(stop_event)
          → for each step:
              sp.turn_on(duration)
              deadline loop (0.5s tick):
                if stop_event.is_set(): return   # aborted
                if sp.state==0 and remaining==0: break  # externally stopped → next step
              sp.turn_off()
      → app_runtime.current_program updated with step counter throughout
```

### Ad-hoc program run
```
POST /adhoc  (or  POST /programs/<id>/run)
  → sched.adhoc_program_run(steps, name)
      → APScheduler 'date' job, runs in ~1 second
      → same execution path as scheduled programs (no rain skip)
```

### Program CRUD
```
POST /programs/save
  → parse form → update app_runtime.programs[id] → _save_conf() → _register_job()
  → _render_programs_partial() returned (HTMX swaps #programs-section)

POST /programs/<id>/delete
  → remove from app_runtime.programs → _save_conf() → remove APScheduler job
  → _render_programs_partial() returned

POST /api/programs/<id>/toggle
  → flip prog["active"] → _save_conf() → _register_job() (adds or removes job)
  → _render_programs_partial() returned
```

### MQTT state feedback
```
OpenBK publishes {prefix}/{channel}/get → OBKMqtt._on_message
  → _on_state(channel, value)
      → sp.state = value
      → if value==0: stop_run(sp.id)
      → if value==1 and no active run: start failsafe run (FAILSAFE_MAX seconds)
      → if value==1 and conflicts with running program: abort_current_program()
```

### UI polling & countdown
```
HTMX polls GET /partial/zones every 3s (authoritative server values)
  → _zones_partial.html rendered with remaining_by_id (M:SS)
  → data-remaining="{{ rem }}" written on .remaining elements

htmx:afterSwap on #zones (base.html JS):
  → clears all existing setInterval handles (window._cdTimers)
  → starts new 1s countdown per active zone, animating M:SS between polls

htmx:afterRequest (base.html JS):
  → matches zone on/off, /adhoc, /programs/<id>/run
  → triggers immediate extra poll of #zones so new state appears instantly
```

---

## Class Relationships

```
app.py (module level)
  owns → sched: Scheduler (timezone from config)
  owns → CONF, ZONES, ZONES_BY_ID, FAILSAFE_MAX, POLL_SEC, TIMEZONE
  helpers → _register_job(), _save_conf(), _programs_view(), _render_programs_partial()
  loads → app_runtime.programs from CONF["programs"] on startup

Scheduler (classes/Scheduler.py)
  wraps → APScheduler BackgroundScheduler
  uses → DayOption, StartTime (value objects, mostly unused now — direct add_job preferred)

jobs.py
  start_scheduled_program() → rain-skip check → start_program_by_id()
  start_program_by_id() → creates Program, sets current_program, runs sequentially

Program (classes/Program.py)
  has → list of (zone_id, seconds) runtimes
  run_sequentially() → deadline loop per step, respects stop_event + external zone-off

Sprinkler (classes/Sprinkler.py)
  has → OBKMqtt reference
  calls → app_runtime.start_run() / stop_run() (lazy import)
  publishes → MQTT set commands

app_runtime
  holds → SPRINKLER_BY_ID, active_runs, current_program, programs,
           last_adhoc_steps, rain_sensor, mqttc
  runs → _failsafe_loop daemon thread
  wires → MQTT on_state_cb → _on_state()
```

---

## Routes Reference

| Method | Path | Returns | Purpose |
|--------|------|---------|---------|
| GET | `/` | dashboard.html | Main page |
| GET | `/partial/zones` | _zones_partial.html | HTMX zone poll |
| GET | `/partial/programs` | _programs_partial.html | Programs section refresh |
| POST | `/zones/<id>/on` | _zones_partial.html | Turn zone on |
| POST | `/zones/<id>/off` | _zones_partial.html | Turn zone off |
| POST | `/adhoc` | redirect → `/` | Run ad-hoc program |
| GET | `/api/zones` | JSON | Zone state |
| GET | `/api/programs` | JSON | All programs |
| POST | `/api/programs` | JSON 201 | Create program (JSON API) |
| PUT | `/api/programs/<id>` | JSON | Update program (JSON API) |
| DELETE | `/api/programs/<id>` | 204 | Delete program (JSON API) |
| POST | `/api/programs/<id>/run` | 204 | Run program immediately (JSON API) |
| POST | `/api/programs/<id>/toggle` | _programs_partial.html | Flip active flag |
| POST | `/programs/save` | _programs_partial.html | Create/update program (form) |
| POST | `/programs/<id>/delete` | _programs_partial.html | Delete program (form) |
| POST | `/programs/<id>/run` | _programs_partial.html | Run program immediately (form) |

---

## Testing Locally (Without Hardware)

1. Run `mock_openbk.py` — reads `zones.yaml` for broker credentials and prefix, simulates OpenBK relay behaviour (single-relay, 600s failsafe, state feedback).
2. Set `mqtt_topic_prefix: sprinkler_test` in `zones.yaml` so both the app and mock use the test prefix, isolated from any real hardware on the same broker.
3. Set `dry_run: false` to enable MQTT (needed for mock to work).

---

## Known Remaining Issues

1. **APScheduler jobstore** — in-memory only. Scheduled jobs are lost on restart. Programs are re-registered from `zones.yaml` on startup, so they recover — but any job that was mid-run or whose `once` trigger date has passed will not re-fire. A SQLite jobstore (`jobs.sqlite` file exists) was previously planned but not configured.

2. **Rain sensor** — `RainSensor` is instantiated and held at module level (`app_runtime.rain_sensor`). However, `get_rain_status()` always returns `False` (TODO stub). No MQTT subscription exists for the rain sensor channel. The rain-skip logic in `start_scheduled_program()` is wired up and will work correctly once `get_rain_status()` is implemented.

3. **`OBKMqtt.get_channel()`** — empty method body (`pass`). No way to query current hardware state on demand; the app relies entirely on MQTT push feedback from OpenBK.

4. **`Scheduler.run_program_by_id()`** — dead code. It calls `program_constructor_from_db()` which now raises `NotImplementedError`. The method is never called anywhere. Can be removed.

5. **`once` schedule expiry** — programs with `schedule.type == "once"` whose date has passed will fail silently on startup (APScheduler will not register a job in the past). No cleanup or UI indication of this state.

---

## Incomplete / Stub Features

- **Rain sensor MQTT** — subscribe to `{prefix}/10/get`, parse value, update `rain_sensor` state. Then implement `get_rain_status()` to return the live value.
- **APScheduler persistence** — configure SQLAlchemy jobstore pointing at `jobs.sqlite` so jobs survive restarts.
- **`once` program cleanup** — after a `once` program fires, mark it inactive or delete it so it doesn't clutter the list.

---

## Deployment

```bash
# On Pi — first deploy
git clone <repo> /home/pi/sprinkler
cd /home/pi/sprinkler
cp zones.yaml.example zones.yaml   # fill in real values
pip install -r requirements.txt
sudo systemctl enable sprinkler
sudo systemctl start sprinkler
```

```bash
# Subsequent deploys — use deploy.sh
./deploy.sh   # git pull + systemctl restart + journal tail
```

Working directory on Pi: `/home/pi/sprinkler`

Systemd unit: `service/sprinkler.service` — waits for MQTT broker on port 1883 before starting, restarts on failure with 5s delay.

---

## Git

- Main branch: `master`
- All active development merged to `master`; `aron` branch is stale
- `zones.yaml` is gitignored — never commit real credentials
