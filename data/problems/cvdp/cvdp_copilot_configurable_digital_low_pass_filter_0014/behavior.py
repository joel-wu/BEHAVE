class FsmLinearReg:
    def __init__(self, DATA_WIDTH=16):
        self.DATA_WIDTH = DATA_WIDTH
        self.reset()

    def reset(self):
        self.state = 0
        self.result1 = 0
        self.result2 = 0
        self.done = 0
        self.buffer1 = 0
        self.buffer2 = 0
        self.buffer_done = 0

    def process(self, start, x_in, w_in, b_in):
        result1 = self.result1
        result2 = self.result2
        done = self.done
        self.result1 = self.buffer1
        self.result2 = self.buffer2
        self.done = self.buffer_done
        if self.state == 0:
            self.buffer1 = 0
            self.buffer2 = 0
            self.buffer_done = 0
            if start:
                self.state = 1
        elif self.state == 1:
            self.buffer1 = (w_in * x_in) >> 1
            self.buffer2 = b_in + (x_in >> 2)
            self.buffer_done = 0
            self.state = 2
        else:
            self.buffer_done = 1
            self.state = 0
        return {"result1": result1, "result2": result2, "done": done}
