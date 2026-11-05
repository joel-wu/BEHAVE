class CompressionHuffmanHuffmanLutDecode8b:
    def __init__(self):
        self.reset()

    def reset(self):
        self.symbols = [0] * 2048
        self.lengths = [0] * 2048

    def process(self, in_write, in_dt_log, in_lookup_bits, in_symbol, in_nb_bits):
        if in_write:
            self.symbols[in_lookup_bits] = in_symbol
            self.lengths[in_lookup_bits] = in_nb_bits
            return None
        if in_dt_log < 1 or in_dt_log > 11:
            return {"out_symbol": 0, "out_nb_bits": 0, "out_error": 1}
        index = in_lookup_bits & ((1 << in_dt_log) - 1)
        length = self.lengths[index]
        if length == 0 or length > in_dt_log:
            return {"out_symbol": 0, "out_nb_bits": 0, "out_error": 1}
        return {"out_symbol": self.symbols[index], "out_nb_bits": length, "out_error": 0}
