class CompressionFseAnsFseEncodeStep:
    """One table-driven FSE symbol step; configuration is ordered with requests."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.delta_bits = [0] * 256
        self.delta_find = [0] * 256
        self.symbol_valid = [0] * 256
        self.states = [0] * 4096
        self.state_valid = [0] * 4096

    def process(self, in_op, in_table_log, in_state, in_symbol,
                in_delta_nb_bits, in_delta_find_state, in_table_index, in_next_state):
        op = in_op & 3
        symbol = in_symbol & 255
        index = in_table_index & 4095
        if op == 1:
            self.delta_bits[symbol] = in_delta_nb_bits & 0xffffffff
            raw = in_delta_find_state & 0xffffffff
            self.delta_find[symbol] = raw - 0x100000000 if raw & 0x80000000 else raw
            self.symbol_valid[symbol] = 1
            return None
        if op == 2:
            self.states[index] = in_next_state & 65535
            self.state_valid[index] = 1
            return None
        if op == 3:
            self.reset()
            return None
        error = {"out_nb_bits": 0, "out_bits": 0, "out_next_state": 0, "out_error": 1}
        log = in_table_log & 15
        state = in_state & 65535
        if not 5 <= log <= 12:
            return error
        size = 1 << log
        if not size <= state < 2 * size or not self.symbol_valid[symbol]:
            return error
        nb = (state + self.delta_bits[symbol]) >> 16
        if nb > log:
            return error
        index = (state >> nb) + self.delta_find[symbol]
        if not 0 <= index < size or not self.state_valid[index]:
            return error
        successor = self.states[index]
        if not size <= successor < 2 * size:
            return error
        return {"out_nb_bits": nb, "out_bits": state & ((1 << nb) - 1),
                "out_next_state": successor, "out_error": 0}
