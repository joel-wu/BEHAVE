class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value):
        both = value & (value >> 1)
        any_pair = (value | (value << 1)) & 1267650600228229401496703205374
        rotated = (value >> 1) | ((value & 1) << 99)
        return {"out_both": both, "out_any": any_pair, "out_different": value ^ rotated}
