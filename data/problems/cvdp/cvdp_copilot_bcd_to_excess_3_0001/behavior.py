class BcdToExcess3:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, bcd):
        if bcd < 10:
            return {"excess3": bcd + 3, "error": 0}
        return {"excess3": 0, "error": 1}
