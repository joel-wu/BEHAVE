class HillCipher:
    def __init__(self):
        self.reset()

    def reset(self):
        self.phase = 0
        self.plain_q = 0
        self.key_q = 0
        self.partial = 0
        self.ciphertext = 0
        self.done = 0

    def process(self, start, plaintext, key):
        ciphertext = self.ciphertext
        done = self.done
        self.done = 0
        if self.phase == 0:
            if start:
                self.plain_q = plaintext
                self.key_q = key
                self.partial = 0
                self.phase = 1
        else:
            row_sum = 0
            for col in range(3):
                p = (self.plain_q >> (10 - col * 5)) & 31
                k = (self.key_q >> (40 - (3 * (self.phase - 1) + col) * 5)) & 31
                row_sum += k * p
            residue = row_sum % 26
            if self.phase == 3:
                self.ciphertext = self.partial | residue
                self.done = 1
                self.phase = 0
            else:
                self.partial |= residue << (15 - self.phase * 5)
                self.phase += 1
        return {"ciphertext": ciphertext, "done": done}
