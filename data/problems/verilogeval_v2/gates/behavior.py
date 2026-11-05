class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b):
        return {"out_and": a & b, "out_or": a | b, "out_xor": a ^ b, "out_nand": (a & b) ^ 1, "out_nor": (a | b) ^ 1, "out_xnor": (a ^ b) ^ 1, "out_anotb": a & (b ^ 1)}
