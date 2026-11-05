class WidthPacker8to16:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pending_byte = 0
        self.have_pending = 0

    def process(self, data_in):
        byte = data_in & 255
        if self.have_pending:
            word = (self.pending_byte << 8) | byte
            self.have_pending = 0
            return {"data_out": word}
        self.pending_byte = byte
        self.have_pending = 1
        return None
