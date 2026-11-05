class Multi16Bit:
    def __init__(self):
        self.reset()

    def reset(self):
        self.areg = 0
        self.breg = 0
        self.product = 0
        self.iteration = 0
        self.done = 0

    def process(self, start, ain, bin):
        product = self.product
        done = self.done
        iteration = self.iteration
        self.done = 0
        if start == 0:
            self.iteration = 0
        elif iteration == 0:
            self.areg = ain
            self.breg = bin
            self.product = 0
            self.iteration = 1
        elif iteration < 17:
            bit = iteration - 1
            if (self.areg >> bit) & 1:
                self.product = (product + (self.breg << bit)) & 4294967295
            self.iteration = iteration + 1
            if iteration == 16:
                self.done = 1
        return {"yout": product, "done": done}
