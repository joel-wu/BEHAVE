class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, addr):
        value = 0
        if addr == 0: value = 41120
        elif addr == 1: value = 45489
        elif addr == 2: value = 49858
        elif addr == 3: value = 54227
        return {"dout": value}
