class Encoder64b66b:
    def __init__(self):
        self.reset()

    def reset(self):
        self.code = 0

    def process(self, encoder_data_in, encoder_control_in):
        old = self.code
        self.code = (1 << 64) | encoder_data_in if encoder_control_in == 0 else 2 << 64
        return {"encoder_data_out": old}
