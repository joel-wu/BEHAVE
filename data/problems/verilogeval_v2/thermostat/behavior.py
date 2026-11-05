class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, mode, too_cold, too_hot, fan_on):
        heater = mode & too_cold
        aircon = (mode ^ 1) & too_hot
        return {"heater": heater, "aircon": aircon, "fan": heater | aircon | fan_on}
