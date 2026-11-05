class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value):
        count = 0
        for i in range(8):
            count += (value >> i) & 1
        return {"parity": count & 1}
