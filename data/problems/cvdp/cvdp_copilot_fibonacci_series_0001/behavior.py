class FibonacciSeries:
    def __init__(self):
        self.reset()

    def reset(self):
        self.reg_a = 0
        self.reg_b = 1
        self.fib_out = 0
        self.pending = 0
        self.overflow_flag = 0

    def process(self):
        fib_out = self.fib_out
        overflow_flag = self.overflow_flag
        if self.pending:
            self.reg_a = 0
            self.reg_b = 1
            self.fib_out = 0
            self.pending = 0
            self.overflow_flag = 1
        else:
            total = self.reg_a + self.reg_b
            self.fib_out = self.reg_b
            self.overflow_flag = 0
            if total >= (1 << 32):
                self.pending = 1
            else:
                self.reg_a = self.reg_b
                self.reg_b = total
        return {"fib_out": fib_out, "overflow_flag": overflow_flag}
