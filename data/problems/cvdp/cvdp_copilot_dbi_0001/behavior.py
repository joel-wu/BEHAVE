class DbiEnc:
    def __init__(self):
        self.reset()

    def reset(self):
        self.previous = 0
        self.control = 0

    def process(self, data_in):
        old_data = self.previous
        old_control = self.control
        encoded = 0
        control = 0
        for lane in range(2):
            shift = lane * 20
            current = (data_in >> shift) & 0xfffff
            difference = current ^ ((old_data >> shift) & 0xfffff)
            count = 0
            for bit in range(20):
                count += (difference >> bit) & 1
            if count > 10:
                current ^= 0xfffff
                control |= 1 << lane
            encoded |= current << shift
        self.previous = encoded
        self.control = control
        return {"data_out": old_data, "dbi_cntrl": old_control}
