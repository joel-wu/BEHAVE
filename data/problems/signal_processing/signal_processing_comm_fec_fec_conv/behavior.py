POLY = (0x6D, 0x4F)


class SignalProcessingCommFecFecConv:
    """Rate-1/2, K=7 stream; every payload byte emits two coded bytes."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.shift_register = 0

    def process(self, in_data, in_last):
        word = 0
        count = 14 if in_last else 8
        for j in range(count):
            bit = ((in_data >> (7-j)) & 1) if j < 8 else 0
            self.shift_register = ((self.shift_register << 1) | bit) & 0x7F
            for polynomial in POLY:
                v = self.shift_register & polynomial
                parity = (v ^ (v >> 1) ^ (v >> 2) ^ (v >> 3) ^
                          (v >> 4) ^ (v >> 5) ^ (v >> 6)) & 1
                word = (word << 1) | parity
        if in_last:
            word <<= 4  # The 28 coded bits need four trailing pad bits.
            self.shift_register = 0
        nbytes = 4 if in_last else 2
        return [{"out_data": (word >> (8*(nbytes-1-i))) & 0xFF}
                for i in range(nbytes)]
