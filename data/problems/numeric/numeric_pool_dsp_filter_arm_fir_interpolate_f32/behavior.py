from fp_lib import fp32_add, fp32_mul

# Time-reversed binomial impulse response, sum 4; all sixteen taps are active.
COEFF = [0x39000000, 0x3AF00000, 0x3C520000, 0x3D638000, 0x3E2AA000, 0x3EBBB000, 0x3F1C6800, 0x3F491800, 0x3F491800, 0x3F1C6800, 0x3EBBB000, 0x3E2AA000, 0x3D638000, 0x3C520000, 0x3AF00000, 0x39000000]


class FirInterpolatorF32L4P4:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = [0, 0, 0, 0]

    def process(self, in_sample):
        self.state = [self.state[1], self.state[2], self.state[3], in_sample]
        outputs = []
        for phase in range(4):
            total = 0
            for tap in range(4):
                total = fp32_add(total, fp32_mul(self.state[tap], COEFF[3-phase+4*tap]))
            outputs.append({"out_sample": total})
        return outputs
