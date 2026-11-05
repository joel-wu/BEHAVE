class SignalProcessingDspMathBesselI0:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x):
        # Unsigned Q8.8 input; the finite numerical domain is [0, 16].
        if in_x > 4096:
            return {"out_y": 0, "out_error": 1}
        square = in_x * in_x
        term = 1 << 80
        total = term
        for k in range(1, 33):
            term = (term * square) // (262144 * k * k)
            total += term
        return {"out_y": total >> 48, "out_error": 0}
