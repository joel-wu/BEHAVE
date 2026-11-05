class BinaryToOneHotDecoder:
    def __init__(self, BINARY_WIDTH=5, OUTPUT_WIDTH=32):
        self.BINARY_WIDTH = BINARY_WIDTH
        self.OUTPUT_WIDTH = OUTPUT_WIDTH

    def reset(self):
        pass

    def process(self, binary_in):
        result = 0
        if binary_in < self.OUTPUT_WIDTH:
            result = 1 << binary_in
        return {"one_hot_out": result}
