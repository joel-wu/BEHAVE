class ChromaSTFT:
    def __init__(self, n_freq=5, n_chroma=12, w_in=32, w_coef=32, w_out=32):
        self.N_FREQ = n_freq
        self.N_CHROMA = n_chroma
        self.W_IN = w_in
        self.W_COEF = w_coef
        self.W_OUT = w_out
        self.reset()

    def reset(self):
        pass

    def process(self, in_s, in_chromafb):
        s = [(in_s >> (f * self.W_IN)) & ((1 << self.W_IN) - 1) for f in range(self.N_FREQ)]
        raw = []
        for c in range(self.N_CHROMA):
            acc = 0
            for f in range(self.N_FREQ):
                coef = (in_chromafb >> (((c * self.N_FREQ + f) * self.W_COEF))) & ((1 << self.W_COEF) - 1)
                acc += s[f] * coef
            raw.append(acc)
        denom = raw[0]
        for c in range(1, self.N_CHROMA):
            if raw[c] > denom:
                denom = raw[c]
        packed = 0
        for c in range(self.N_CHROMA):
            val = 0 if denom == 0 else ((raw[c] << 16) // denom)
            packed |= (val & ((1 << self.W_OUT) - 1)) << (c * self.W_OUT)
        return {"out_chroma": packed}
