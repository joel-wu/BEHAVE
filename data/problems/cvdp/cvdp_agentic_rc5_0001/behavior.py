class Rc5Enc16bit:
    def __init__(self):
        self.reset()

    def reset(self):
        self.ca = 255
        self.phase = 0
        self.a = 0
        self.b = 0
        self.s2 = 0
        self.s3 = 0
        self.ciphertext = 0
        self.done = 0

    def process(self, enc_start, p):
        c = self.ciphertext
        enc_done = self.done
        self.done = 0
        ca1 = ((self.ca << 1) ^ (self.ca >> 1) ^ (self.ca & 42)) & 255
        if self.phase == 0:
            if enc_start:
                ca2 = ((ca1 << 1) ^ (ca1 >> 1) ^ (ca1 & 42)) & 255
                ca3 = ((ca2 << 1) ^ (ca2 >> 1) ^ (ca2 & 42)) & 255
                self.a = ((p >> 8) + self.ca) & 255
                self.b = ((p & 255) + ca1) & 255
                self.s2 = ca2
                self.s3 = ca3
                self.phase = 1
        elif self.phase == 1:
            value = self.a ^ self.b
            amount = self.b & 7
            self.a = (((value << amount) | (value >> (8-amount))) + self.s2) & 255
            self.phase = 2
        elif self.phase == 2:
            value = self.b ^ self.a
            amount = self.a & 7
            self.b = (((value << amount) | (value >> (8-amount))) + self.s3) & 255
            self.phase = 3
        else:
            self.ciphertext = (self.a << 8) | self.b
            self.done = 1
            self.phase = 0
        self.ca = ca1
        return {"c": c, "enc_done": enc_done}
