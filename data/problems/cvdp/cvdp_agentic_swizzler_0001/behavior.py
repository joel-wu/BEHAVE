class Swizzler:
    def __init__(self, NUM_LANES=4, DATA_WIDTH=8, REGISTER_OUTPUT=0, ENABLE_PARITY_CHECK=0):
        self.NUM_LANES = NUM_LANES
        self.DATA_WIDTH = DATA_WIDTH
        self.REGISTER_OUTPUT = REGISTER_OUTPUT
        self.ENABLE_PARITY_CHECK = ENABLE_PARITY_CHECK
        self.INDEX_WIDTH = max(1, (NUM_LANES - 1).bit_length())
        self.reset()

    def reset(self):
        self.saved_data = 0
        self.saved_parity = 0

    def process(self, bypass, data_in, swizzle_map_flat):
        packed = 0
        error = 0
        for i in range(self.NUM_LANES):
            index = (swizzle_map_flat >> (i * self.INDEX_WIDTH)) & ((1 << self.INDEX_WIDTH) - 1)
            if bypass:
                index = i
            lane = 0
            if index < self.NUM_LANES:
                lane = (data_in >> (index * self.DATA_WIDTH)) & ((1 << self.DATA_WIDTH) - 1)
            packed |= lane << (i * self.DATA_WIDTH)
            parity = 0
            for j in range(self.DATA_WIDTH):
                parity ^= (lane >> j) & 1
            if self.ENABLE_PARITY_CHECK:
                error |= parity
        if self.REGISTER_OUTPUT:
            out_data = self.saved_data
            out_parity = self.saved_parity
            self.saved_data = packed
            self.saved_parity = error
        else:
            out_data = packed
            out_parity = error
        return {"data_out": out_data, "parity_error": out_parity}
