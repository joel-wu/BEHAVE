class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, do_sub, a, b):
        out = ((a - b) if do_sub else (a + b)) & 255
        return {"out": out, "result_is_zero": int(out == 0)}
