class ContinuousAdder:
    def __init__(self, DATA_WIDTH=32, ENABLE_THRESHOLD=0, THRESHOLD=16, REGISTER_OUTPUT=0):
        self.DATA_WIDTH = DATA_WIDTH
        self.ENABLE_THRESHOLD = ENABLE_THRESHOLD
        self.THRESHOLD = THRESHOLD
        self.REGISTER_OUTPUT = REGISTER_OUTPUT
        self.reset()

    def reset(self):
        self.total=0
        self.output=0
        self.valid=0

    def process(self, valid_in, data_in, accumulate_enable, flush):
        qualified=int(not self.ENABLE_THRESHOLD or self.total>=self.THRESHOLD)
        value=self.total if qualified else 0
        if self.REGISTER_OUTPUT:
            sum_out=self.output
            sum_valid=self.valid
            self.output=value
            self.valid=qualified
        else:
            sum_out=value
            sum_valid=qualified
        if flush:
            self.total=0
        elif valid_in and accumulate_enable:
            self.total=(self.total+data_in)&((1<<self.DATA_WIDTH)-1)
        return {"sum_out":sum_out,"sum_valid":sum_valid}
