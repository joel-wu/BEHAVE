class LittleEndianU32Reader:
    def __init__(self):
        self.reset()

    def reset(self):
        self.count = 0
        self.word = 0

    def process(self, in_byte):
        self.word |= (in_byte & 255) << (8*self.count)
        if self.count == 3:
            result = self.word
            self.word = 0
            self.count = 0
            return {"out_word": result}
        self.count += 1
        return None
