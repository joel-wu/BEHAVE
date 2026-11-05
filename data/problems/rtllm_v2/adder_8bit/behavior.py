class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, cin):
        total = a + b + cin
        return {"sum": total & 255, "cout": total >> 8}
