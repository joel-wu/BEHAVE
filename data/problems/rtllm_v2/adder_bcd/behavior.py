class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, A, B, Cin):
        total = A + B + Cin
        carry = int(total > 9)
        return {"Sum": (total + 6 * carry) & 15, "Cout": carry}
