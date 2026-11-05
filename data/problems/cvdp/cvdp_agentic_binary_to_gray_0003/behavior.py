class BinaryToGray:
    def __init__(self, WIDTH=6):
        self.WIDTH=WIDTH

    def reset(self):
        pass

    def process(self, binary_in):
        return {"gray_out": binary_in^(binary_in>>1)}
