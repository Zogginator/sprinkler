import json, threading, time, re
import paho.mqtt.client as mqtt
import logging  


class OBKMqtt:
    """
    Sprinkler channel-topicos séma:
      publish:  sprinkler/{channel}/set   payload: "1" vagy "0"
      state:    sprinkler/{channel}/get   payload: "1" vagy "0" (feliratkozás: sprinkler/+/get)
    """

    def __init__(
        self,
        host,
        port,
        username,
        password,
        qos,
        set_tmpl,
        state_sub,
        on_state_cb=None,
        dry_run=False,
        logger=None,
    ):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.qos = qos
        self.set_tmpl = set_tmpl  # pl.: sprinkler/{channel}/set
        self.get_tmpl = set_tmpl.replace("/set", "/get")  # pl.: sprinkler/{channel}/get
        self.state_sub = state_sub  # pl.: sprinkler/+/get
        self.on_state_cb = on_state_cb  # callback(channel:int, value:int)
        self.dry_run = dry_run

        self.logger = logger or logging.getLogger(__name__)

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"sprinkler-backend-{int(time.time())}",
        )
        if username:
            self.client.username_pw_set(username, password)

        self._thread = None
        # Build topic regex from state_sub: "prefix/+/get" → "^prefix/(\d+)/get$"
        _prefix = state_sub.rsplit("/+/", 1)[0]
        self._topic_re = re.compile(rf"^{re.escape(_prefix)}/(\d+)/get$")

    def start(self):
        if self.dry_run:
            self.logger.info("[DRY RUN] MQTT client not started — no broker connection will be made")
            self.logger.info("[DRY RUN] would subscribe to %s", self.state_sub)
            return
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            try:
                self.logger.info("MQTT connecting to %s:%s", self.host, self.port)
                self.client.connect(self.host, self.port, keepalive=30)
                self.client.loop_forever()
            except Exception as e:
                self.logger.warning("MQTT connection failed: %s — retry in 10s", e)
                time.sleep(10)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        client.subscribe(self.state_sub, qos=self.qos)

    def _on_message(self, client, userdata, msg):
        # topic: sprinkler/<channel>/get
        # payload: "0" vagy "1"
        try:
            payload = msg.payload.decode("utf-8").strip()
            val = 1 if payload in ("1", "ON", "on", "true", "True") else 0
            m = self._topic_re.match(msg.topic)
            if m:
                ch = int(m.group(1))
                if self.on_state_cb:
                    self.on_state_cb(ch, val)
        except Exception as e:
            self.logger.warning(f"state parse error: {e}")

    def set_channel(self, channel: int, value: int):
        topic = self.set_tmpl.format(channel=channel)
        payload = "1" if int(value) == 1 else "0"
        if self.dry_run:
            self.logger.info("[DRY RUN] would publish: %s = %s", topic, payload)
            return
        self.client.publish(topic, payload, qos=self.qos, retain=False)

    def get_channel(self, channel: int):
        if self.dry_run:
            self.logger.info("[DRY RUN] would request: %s", self.get_tmpl.format(channel=channel))
            return