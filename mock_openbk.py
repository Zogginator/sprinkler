"""
mock_openbk.py — Simulates OpenBK7231N autoexec relay behavior for testing.

Connects to the same Mosquitto broker as the main app. Subscribes to
sprinkler/+/set, publishes state back on sprinkler/{channel}/get.

Autoexec rules mirrored:
  - Only one relay ON at a time (turning on a new one turns off any active one)
  - 600-second hardware failsafe per relay (auto-OFF if not cancelled)
  - State published on every change

Usage:
  python3 mock_openbk.py [--host HOST] [--port PORT]
  Default host/port read from zones.yaml (falls back to localhost:1883)
"""

import argparse
import logging
import re
import threading
import time

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Config (set dynamically in main() from zones.yaml)
# ---------------------------------------------------------------------------
CHANNELS: list[int] = []
FAILSAFE_SECONDS: int = 600
SET_TOPIC: str = ""
GET_TOPIC: str = ""
SUBSCRIBE_WILDCARD: str = ""
_PREFIX: str = ""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mock_openbk")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
_state: dict[int, int] = {}   # channel → 0/1  (populated after config load)
_timers: dict[int, threading.Timer] = {}               # channel → failsafe Timer
_lock = threading.Lock()
_client: mqtt.Client | None = None


def _publish_state(channel: int, value: int):
    topic = GET_TOPIC.format(channel=channel)
    _client.publish(topic, str(value), qos=1, retain=True)


def _cancel_timer(channel: int):
    t = _timers.pop(channel, None)
    if t:
        t.cancel()


def _failsafe_off(channel: int):
    with _lock:
        if _state.get(channel) == 1:
            _state[channel] = 0
            _timers.pop(channel, None)
            _publish_state(channel, 0)
            log.info("[MOCK] channel %d OFF (failsafe triggered)", channel)


def _turn_on(channel: int):
    """Turn on channel; turn off any currently active channel first."""
    # Turn off any other active channel
    for ch, val in list(_state.items()):
        if ch != channel and val == 1:
            _state[ch] = 0
            _cancel_timer(ch)
            _publish_state(ch, 0)
            log.info("[MOCK] channel %d OFF — turned off before channel %d", ch, channel)

    if _state[channel] == 1:
        log.info("[MOCK] channel %d already ON, refreshing failsafe", channel)
        _cancel_timer(channel)
    else:
        _state[channel] = 1
        _publish_state(channel, 1)
        log.info("[MOCK] channel %d ON (failsafe: %ds)", channel, FAILSAFE_SECONDS)

    # Start (or restart) failsafe timer
    t = threading.Timer(FAILSAFE_SECONDS, _failsafe_off, args=(channel,))
    t.daemon = True
    t.start()
    _timers[channel] = t


def _turn_off(channel: int):
    if _state[channel] == 0:
        log.info("[MOCK] channel %d already OFF", channel)
        return
    _state[channel] = 0
    _cancel_timer(channel)
    _publish_state(channel, 0)
    log.info("[MOCK] channel %d OFF", channel)


# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------
def _on_connect(client, userdata, flags, reason_code, properties=None):
    client.subscribe(SUBSCRIBE_WILDCARD, qos=1)
    log.info("[MOCK] connected to broker, subscribed to %s", SUBSCRIBE_WILDCARD)
    # Publish current state for all channels
    for ch in CHANNELS:
        _publish_state(ch, _state[ch])


def _on_message(client, userdata, msg):
    m = re.match(rf"^{re.escape(_PREFIX)}/(\d+)/set$", msg.topic)
    if not m:
        return
    channel = int(m.group(1))
    if channel not in CHANNELS:
        log.warning("[MOCK] received command for unknown channel %d, ignoring", channel)
        return
    payload = msg.payload.decode("utf-8").strip()
    value = 1 if payload in ("1", "ON", "on", "true", "True") else 0
    with _lock:
        if value == 1:
            _turn_on(channel)
        else:
            _turn_off(channel)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    global _client, CHANNELS, FAILSAFE_SECONDS, SET_TOPIC, GET_TOPIC, SUBSCRIBE_WILDCARD, _PREFIX, _state

    parser = argparse.ArgumentParser(description="Mock OpenBK7231N relay simulator")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    args = parser.parse_args()

    # Try to read broker config from zones.yaml
    host, port, username, password = "localhost", 1883, None, None
    try:
        import yaml, os
        conf_path = os.environ.get("ZONES_CONF", "zones.yaml")
        with open(conf_path, "r", encoding="utf-8") as f:
            conf = yaml.safe_load(f)
        host = conf["mqtt"]["host"]
        port = int(conf["mqtt"]["port"])
        username = conf["mqtt"].get("username") or None
        password = conf["mqtt"].get("password") or None
        _PREFIX = conf["mqtt"].get("mqtt_topic_prefix", "sprinkler")
        CHANNELS = [z["channel"] for z in conf.get("zones", [])]
        FAILSAFE_SECONDS = int(conf.get("failsafe", {}).get("max_seconds", 600))
    except Exception as e:
        log.warning("Could not read zones.yaml (%s), using defaults", e)
        _PREFIX = "sprinkler"
        CHANNELS = [31, 32, 33]

    SET_TOPIC = f"{_PREFIX}/{{channel}}/set"
    GET_TOPIC = f"{_PREFIX}/{{channel}}/get"
    SUBSCRIBE_WILDCARD = f"{_PREFIX}/+/set"
    _state = {ch: 0 for ch in CHANNELS}
    log.info("[MOCK] prefix=%s channels=%s failsafe=%ds", _PREFIX, CHANNELS, FAILSAFE_SECONDS)

    # CLI args override yaml
    if args.host:
        host = args.host
    if args.port:
        port = args.port
    if args.username:
        username = args.username
    if args.password:
        password = args.password

    _client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="mock-openbk",
    )
    if username:
        _client.username_pw_set(username, password)
    _client.on_connect = _on_connect
    _client.on_message = _on_message

    log.info("[MOCK] connecting to %s:%d ...", host, port)
    _client.connect(host, port, keepalive=30)

    try:
        _client.loop_forever()
    except KeyboardInterrupt:
        log.info("[MOCK] shutting down")
        _client.disconnect()


if __name__ == "__main__":
    main()
