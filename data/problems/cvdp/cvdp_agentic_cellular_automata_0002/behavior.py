class PseudoRandGeneratorCa:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, load_seed, CA_seed, rule_sel):
        out = {"CA_out": self.q}
        if load_seed:
            self.q = CA_seed
        else:
            left = ((self.q << 1) | (self.q >> 15)) & 65535
            right = (self.q >> 1) | ((self.q & 1) << 15)
            if rule_sel == 1:
                self.q = (self.q ^ right) | ((left ^ 65535) & self.q)
            else:
                self.q = left ^ (self.q | right)
        return out
