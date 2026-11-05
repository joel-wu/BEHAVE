class ConvolutionalEncoder:
    def __init__(self):
        self.reset()

    def reset(self):
        self.history = 0
        self.first = 0
        self.second = 0

    def process(self, data_in):
        first = self.first
        second = self.second
        self.first = data_in ^ (self.history & 1) ^ ((self.history >> 1) & 1)
        self.second = data_in ^ ((self.history >> 1) & 1)
        self.history = ((self.history << 1) | data_in) & 3
        return {"encoded_bit1": first, "encoded_bit2": second}
