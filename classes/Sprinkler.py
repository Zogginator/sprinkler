import logging


class RainSensor:
    def __init__(self, mqttc, channel):
        self.mqttc = mqttc
        self.channel = channel

    def get_rain_status(self):
        # TODO: implement via MQTT
        return False


class Sprinkler:
    def __init__(self, id, name, channel, mqttc, logger=None):
        self.id = id
        self.name = name
        self.channel = channel
        self.mqttc = mqttc
        self.state = 0  # updated by MQTT feedback; set optimistically on turn_on/off
        self.logger = logger or logging.getLogger(__name__)

    def turn_on(self, seconds: int):
        import app_runtime
        self.state = 1
        self.mqttc.set_channel(self.channel, 1)
        app_runtime.start_run(self.id, seconds)
        self.logger.info("Turning on %s (channel %d) for %ds", self.name, self.channel, seconds)

    def turn_off(self):
        import app_runtime
        self.state = 0
        app_runtime.stop_run(self.id)
        self.mqttc.set_channel(self.channel, 0)
        self.logger.info("Turning off %s (channel %d)", self.name, self.channel)
