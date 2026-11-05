class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value):
        both = value & (value >> 1)
        any_pair = (value | (value << 1)) & 14
        rotated = (value >> 1) | ((value & 1) << 3)
        return {"out_both": both, "out_any": any_pair, "out_different": value ^ rotated}
