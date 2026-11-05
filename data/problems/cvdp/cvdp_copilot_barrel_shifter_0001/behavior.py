class BarrelShifter8bit:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, data_in, shift_bits, left_right):
        result = data_in << shift_bits if left_right else data_in >> shift_bits
        return {"data_out": result & 255}
