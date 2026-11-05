class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, ring, vibrate_mode):
        return {"ringer": ring & (vibrate_mode ^ 1), "motor": ring & vibrate_mode}
