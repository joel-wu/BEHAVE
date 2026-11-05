class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, sel_b1, sel_b2):
        out = b if sel_b1 and sel_b2 else a
        return {"out_assign": out, "out_always": out}
