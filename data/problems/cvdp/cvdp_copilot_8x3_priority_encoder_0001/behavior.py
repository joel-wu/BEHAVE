class PriorityEncoder8x3:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value):
        out = 0
        for bit_index in range(8):
            if (value >> bit_index) & 1:
                out = bit_index
        return {"out": out}
