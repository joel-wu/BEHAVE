class Piso8Bit:
    def __init__(self):
        self.reset()

    def reset(self):
        self.tmp = 1
        self.bit_index = 0
        self.serial_out = 0

    def process(self):
        serial_out = self.serial_out
        self.serial_out = (self.tmp >> (7 - self.bit_index)) & 1
        if self.bit_index == 7:
            self.tmp = (self.tmp + 1) & 255
        self.bit_index = (self.bit_index + 1) & 7
        return {"serial_out": serial_out}
