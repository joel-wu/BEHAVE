class SyncLifo:
    def __init__(self, DATA_WIDTH=8, ADDR_WIDTH=3):
        self.DATA_WIDTH = DATA_WIDTH
        self.ADDR_WIDTH = ADDR_WIDTH
        self.reset()

    def reset(self):
        self.memory = [0] * (1 << self.ADDR_WIDTH)
        self.count = 0
        self.data_out = 0

    def process(self, write_en, read_en, data_in):
        empty = int(self.count == 0)
        full = int(self.count == (1 << self.ADDR_WIDTH))
        data_out = self.data_out
        pop = read_en and not empty
        push = write_en and not full
        position = self.count
        if pop:
            position -= 1
            self.data_out = self.memory[position]
        if push:
            self.memory[position] = data_in
            position += 1
        self.count = position
        return {"empty": empty, "full": full, "data_out": data_out}
