class BinaryToOneHotDecoderSequential:
    def __init__(self, BINARY_WIDTH=5, OUTPUT_WIDTH=32):
        self.BINARY_WIDTH = BINARY_WIDTH
        self.OUTPUT_WIDTH = OUTPUT_WIDTH
        self.reset()

    def reset(self):
        self.one_hot = 0

    def process(self, i_binary_in):
        o_one_hot_out = self.one_hot
        self.one_hot = (1 << i_binary_in) if i_binary_in < self.OUTPUT_WIDTH else 0
        return {"o_one_hot_out": o_one_hot_out}
