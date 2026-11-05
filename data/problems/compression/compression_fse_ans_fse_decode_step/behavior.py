class CompressionFseAnsFseDecodeStep:
    """One FSE decoding-table lookup and state update."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.symbols = [0] * 4096
        self.counts = [0] * 4096
        self.bases = [0] * 4096
        self.valid = [0] * 4096

    def process(self, in_op, in_table_log, in_state, in_bits,
                in_table_index, in_symbol, in_nb_bits, in_new_state):
        op = in_op & 3
        index = in_table_index & 4095
        if op == 1:
            self.symbols[index] = in_symbol & 255
            self.counts[index] = in_nb_bits & 31
            self.bases[index] = in_new_state & 65535
            self.valid[index] = 1
            return None
        if op == 2:
            self.reset()
            return None
        error = {"out_symbol": 0, "out_next_state": 0,
                 "out_bits_consumed": 0, "out_error": 1}
        log = in_table_log & 15
        state = in_state & 65535
        if op == 3 or not 5 <= log <= 12:
            return error
        size = 1 << log
        if state >= size or not self.valid[state]:
            return error
        count = self.counts[state]
        if count > log:
            return error
        successor = self.bases[state] + ((in_bits & 0xffffffff) & ((1 << count) - 1))
        if successor >= size:
            return error
        return {"out_symbol": self.symbols[state], "out_next_state": successor,
                "out_bits_consumed": count, "out_error": 0}
