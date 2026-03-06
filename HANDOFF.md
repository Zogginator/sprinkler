# Sprinkler Controller — Project Handoff

## Overview

A garden sprinkler controller running on a Raspberry Pi. It controls irrigation zones via MQTT, publishing on/off commands to an OpenBK7231N smart relay device. A Flask web UI lets the user manually trigger zones, run ad-hoc programs, and view live zone state. APScheduler (in-memory) provides cron scheduling. The project language mix: Hungarian variable names/UI labels + English code.

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
- **Rain sensor** — channel 10, wired but not yet implemented
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
app.py                  # Flask app + main entry point; starts API thread + APScheduler
app_runtime.py          # Shared runtime state: MQTT client, sprinkler objects, timing
jobs.py                 # Top-level job function for APScheduler (must be importable at top level)
zones.yaml              # Hardware config — GITIGNORED, create from zones.yaml.example
zones.yaml.example      # Template config (committed, no secrets)

classes/
  Sprinkler.py          # Sprinkler, RainSensor — simple MQTT control + state
  Program.py            # Program class: sequential zone execution via time.sleep
  Scheduler.py          # APScheduler wrapper + DayOption/StartTime value objects

mqtt_client.py          # OBKMqtt: paho-mqtt wrapper for the set/get topic scheme
mock_openbk.py          # Standalone MQTT relay simulator for hardware-free testing
deploy.sh               # Pi deploy: git pull + systemctl restart + journal tail
requirements.txt        # Python dependencies
service/sprinkler.service  # systemd unit (waits for MQTT broker before starting)

templates/
  base.html             # HTML shell; JS saves/restores duration inputs across polls
  dashboard.html        # Main page; zone grid polls /partial/zones every 3s via HTMX
  _zones_partial.html   # Zone cards partial (BE/KI forms, countdown, program status)
static/main.css         # Mobile-first CSS (1-col → 3-col grid)
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
                                    # real OpenBK listens on sprinkler/+/set only

zones:
  - id: 1
    name: "Zone 1"
    channel: 31

failsafe:
  max_seconds: 600

timezone: "Europe/Budapest"
dry_run: false   # set true to skip MQTT publish (logs instead)
```

**`mqtt_topic_prefix`**: All MQTT topics are derived from this prefix at startup. Set to `sprinkler_test` locally so the real OpenBK hardware ignores test traffic. Production uses `sprinkler`.

---

## State Model

Everything flows through two sources of truth:

1. **`sp.state`** (on `Sprinkler` object) — hardware ON/OFF state. Set optimistically on `turn_on()`/`turn_off()`, then confirmed by MQTT feedback via `_on_state()`.

2. **`app_runtime.active_runs`** — `dict[zone_id, {started_at: float, duration: int}]`. Tracks when each zone started and for how long. `remaining(zone_id)` computes seconds left. `_failsafe_loop` auto-calls `turn_off()` when remaining hits zero.

There are no `SprinklerRun` objects, no threading timers, no cleanup daemons.

---

## Key Data Flow

### Manual zone on/off
```
POST /zones/<id>/on
  → Sprinkler.turn_on(seconds)
      → sp.state = 1 (optimistic)
      → OBKMqtt.set_channel(channel, 1)    # MQTT publish
      → app_runtime.start_run(id, seconds) # timing entry
  → partial_zones() rendered and returned
```

```
POST /zones/<id>/off
  → Sprinkler.turn_off()
      → sp.state = 0
      → app_runtime.stop_run(id)
      → OBKMqtt.set_channel(channel, 0)
  → partial_zones() rendered and returned
```

### Failsafe auto-off
```
_failsafe_loop (daemon thread, 1s tick)
  → for each active_run: if remaining(zone_id) == 0: sp.turn_off()
```

### Scheduled programs
```
APScheduler cron → jobs.start_program_by_id(steps=[(zone_id, seconds), ...])
  → Program.run_sequentially()
      → for each step:
          sp.turn_on(duration)
          time.sleep(duration)  # checks stop_event every 1s for abort
          sp.turn_off()
  → app_runtime.current_program updated with step counter throughout
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

### UI polling
```
HTMX polls GET /partial/zones every 3s
  → remaining_by_id computed from app_runtime.remaining(zone_id) for all zones
  → _zones_partial.html rendered with live state + server-side countdown (M:SS)
  → OOB swap updates the ad-hoc Run button disabled state
```

---

## Class Relationships

```
Scheduler
  uses → Program (via jobs.start_program_by_id)
  uses → DayOption, StartTime (value objects)

Program
  has → list of (zone_id, seconds) runtimes
  calls → Sprinkler.turn_on() / turn_off() directly
  reads → app_runtime.SPRINKLER_BY_ID (lazy, at run time)

Sprinkler
  has → OBKMqtt reference
  calls → app_runtime.start_run() / stop_run() (lazy import)
  publishes → MQTT set commands

app_runtime
  holds → SPRINKLER_BY_ID, active_runs, current_program
  runs → _failsafe_loop daemon thread
  wires → MQTT on_state_cb → _on_state()
```

---

## Testing Locally (Without Hardware)

1. Run `mock_openbk.py` — reads `zones.yaml` for broker credentials and prefix, simulates OpenBK relay behaviour (single-relay, 600s failsafe, state feedback).
2. Set `mqtt_topic_prefix: sprinkler_test` in `zones.yaml` so both the app and mock use the test prefix, isolated from any real hardware on the same broker.
3. Set `dry_run: false` to enable MQTT (needed for mock to work).

---

## Known Remaining Issues

1. **`Scheduler.py:6`** — `TZ = ZoneInfo("Europe/Budapest")` is hardcoded. The `timezone` value from `zones.yaml` is loaded in `app.py` as `TIMEZONE` but never passed to `Scheduler`. All scheduled jobs use the hardcoded timezone regardless of config.

2. **`Scheduler.run_program_by_id()` (`Scheduler.py:117`)** — calls `p.cleanup()` which no longer exists on `Program` (removed in the SprinklerRun refactor). This method will raise `AttributeError` if called. It is not used anywhere currently.

3. **`POLL_SEC` config key (`app.py:31`)** — reads `CONF.get("poll_seconds", 3)` from the top-level yaml key, but the actual yaml structure has it under `failsafe.poll_seconds`. The default (3s) is always used; the configured value is ignored.

4. **`program_constructor_from_db()` (`Program.py:9`)** — still a stub returning hardcoded `[(3, 10), (2, 10)]`. Scheduled jobs that load programs by ID from a DB will silently run the wrong program.

5. **`app.py:41-42`** — `program1` and `test2` are test artifacts instantiated at module level. These run at import time and should be removed before production deployment.

6. **Rain sensor** — `RainSensor` is instantiated in `init_runtime` but the result is stored only in a local variable and immediately garbage collected. No MQTT subscription is set up for rain sensor state, and no rain-skip logic exists in the scheduler.

7. **APScheduler jobstore** — currently in-memory only; scheduled jobs are lost on restart. A persistent SQLite jobstore was previously planned (`jobs.sqlite` file exists in repo) but is not configured.

8. **Schedule CRUD** — no UI or API to add/remove scheduled programs. Schedules are hardcoded in `app.py` at module level.

---

## Incomplete / Stub Features

- **Program persistence** — programs are defined in code. No DB schema or UI for managing them.
- **Schedule management UI** — two `POST /api/schedule` endpoints are commented out in `app.py`. No frontend for adding/removing schedules.
- **`OBKMqtt.get_channel()`** — empty method body (`pass`). No way to query current hardware state on demand.

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
