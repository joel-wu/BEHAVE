class FiloRtl:
    def __init__(self, DATA_WIDTH=8, FILO_DEPTH=16):
        self.DATA_WIDTH = DATA_WIDTH
        self.FILO_DEPTH = FILO_DEPTH
        self.reset()

    def reset(self):
        self.storage = [0] * self.FILO_DEPTH
        self.count = 0
        self.output = 0

    def process(self, push, pop, data_in):
        data_out = self.output
        full = int(self.count == self.FILO_DEPTH)
        empty = int(self.count == 0)
        if push and pop and empty:
            self.output = data_in
        elif pop and not empty:
            self.output = self.storage[self.count - 1]
            if push and not full:
                self.storage[self.count - 1] = data_in
            else:
                self.count -= 1
        elif push and not full:
            self.storage[self.count] = data_in
            self.count += 1
        return {"data_out": data_out, "full": full, "empty": empty}
