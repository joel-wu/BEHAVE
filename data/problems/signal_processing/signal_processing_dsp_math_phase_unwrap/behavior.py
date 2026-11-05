def signed64(v):
    return ((v + (1 << 63)) & ((1 << 64)-1)) - (1 << 63)


class SignalProcessingDspMathPhaseUnwrap:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_n, in_lev, in_mar, in_phi0, in_phi1, in_phi2, in_phi3, in_phi4, in_phi5, in_phi6, in_phi7):
        phases = [signed64(x) for x in (in_phi0, in_phi1, in_phi2, in_phi3, in_phi4, in_phi5, in_phi6, in_phi7)]
        lev, margin = signed64(in_lev), signed64(in_mar)
        status = 0
        if in_n < 1 or in_n > 8:
            status = 1
        elif lev <= 0 or margin < 32768:
            status = 2
        result = list(phases)
        if status == 0:
            threshold = margin * lev
            period = lev * 65536
            correction = 0
            for i in range(1, 8):
                if i < in_n:
                    delta = phases[i] - phases[i-1]
                    excess = abs(delta) * 65536 - threshold
                    if excess > 0:
                        count = (excess + period - 1) // period
                        correction += -count * lev if delta > 0 else count * lev
                    result[i] = phases[i] + correction
        return {"out_status": status,
                "out_phi0": result[0] & ((1 << 128)-1),
                "out_phi1": result[1] & ((1 << 128)-1),
                "out_phi2": result[2] & ((1 << 128)-1),
                "out_phi3": result[3] & ((1 << 128)-1),
                "out_phi4": result[4] & ((1 << 128)-1),
                "out_phi5": result[5] & ((1 << 128)-1),
                "out_phi6": result[6] & ((1 << 128)-1),
                "out_phi7": result[7] & ((1 << 128)-1)}
