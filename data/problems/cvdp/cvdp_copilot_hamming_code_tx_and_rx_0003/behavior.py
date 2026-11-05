class HammingCodeReceiver:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, data_in):
        c1 = ((data_in >> 1) ^ (data_in >> 3) ^ (data_in >> 5) ^ (data_in >> 7)) & 1
        c2 = ((data_in >> 2) ^ (data_in >> 3) ^ (data_in >> 6) ^ (data_in >> 7)) & 1
        c4 = ((data_in >> 4) ^ (data_in >> 5) ^ (data_in >> 6) ^ (data_in >> 7)) & 1
        syndrome = c1 | (c2 << 1) | (c4 << 2)
        corrected = data_in
        if syndrome:
            corrected ^= 1 << syndrome
        return {"data_out": ((corrected >> 4) & 14) | ((corrected >> 3) & 1)}
