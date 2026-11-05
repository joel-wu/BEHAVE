class LifoBuffer:
    def __init__(self):
        self.reset()

    def reset(self):
        self.words = [0]*4
        self.count = 0
        self.output = 0

    def process(self, dataIn, RW, EN, clear):
        out = {"EMPTY": int(self.count == 0), "FULL": int(self.count == 4), "dataOut": self.output}
        if EN:
            self.output = 0
            if clear:
                self.count = 0
                for i in range(4):
                    self.words[i] = 0
            elif RW == 0 and self.count < 4:
                self.words[self.count] = dataIn
                self.count += 1
            elif RW and self.count > 0:
                self.count -= 1
                self.output = self.words[self.count]
                self.words[self.count] = 0
        return out
