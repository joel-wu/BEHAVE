class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, load, data):
        out = {"q": self.q}
        if load:
            self.q = data
        else:
            mask = (1 << 256) - 1
            column0 = mask // 65535
            column15 = column0 << 15
            above = ((self.q << 16) & mask) | (self.q >> 240)
            below = (self.q >> 16) | ((self.q & 65535) << 240)
            west = ((self.q << 1) & (mask ^ column0)) | ((self.q >> 15) & column0)
            east = ((self.q >> 1) & (mask ^ column15)) | ((self.q & column0) << 15)
            nw = ((above << 1) & (mask ^ column0)) | ((above >> 15) & column0)
            ne = ((above >> 1) & (mask ^ column15)) | ((above & column0) << 15)
            sw = ((below << 1) & (mask ^ column0)) | ((below >> 15) & column0)
            se = ((below >> 1) & (mask ^ column15)) | ((below & column0) << 15)
            ones = 0
            twos = 0
            fours = 0
            eights = 0
            for neighbor in [above, below, west, east, nw, ne, sw, se]:
                carry1 = ones & neighbor
                ones = ones ^ neighbor
                carry2 = twos & carry1
                twos = twos ^ carry1
                carry3 = fours & carry2
                fours = fours ^ carry2
                eights = eights ^ carry3
            self.q = twos & ((fours | eights) ^ mask) & (ones | self.q)
        return out
