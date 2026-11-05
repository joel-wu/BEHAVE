class HammingCodeTxFor4bit:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, data_in):
        d0 = data_in & 1
        d1 = (data_in >> 1) & 1
        d2 = (data_in >> 2) & 1
        d3 = (data_in >> 3) & 1
        result = ((d0 ^ d1 ^ d3) << 1) | ((d0 ^ d2 ^ d3) << 2) | (d0 << 3) | ((d1 ^ d2 ^ d3) << 4) | (d1 << 5) | (d2 << 6) | (d3 << 7)
        return {"data_out": result}
