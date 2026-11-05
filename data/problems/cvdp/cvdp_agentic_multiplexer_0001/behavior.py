class Multiplexer:
    def __init__(self, DATA_WIDTH=8, NUM_INPUTS=4, REGISTER_OUTPUT=0, HAS_DEFAULT=0, DEFAULT_VALUE=0):
        self.DATA_WIDTH = DATA_WIDTH
        self.NUM_INPUTS = NUM_INPUTS
        self.REGISTER_OUTPUT = REGISTER_OUTPUT
        self.HAS_DEFAULT = HAS_DEFAULT
        self.DEFAULT_VALUE = DEFAULT_VALUE
        self.reset()

    def reset(self):
        self.previous=0

    def process(self, inp, sel, bypass):
        mask=(1<<self.DATA_WIDTH)-1
        index=0 if bypass else sel
        if index<self.NUM_INPUTS:
            selected=(inp>>(index*self.DATA_WIDTH))&mask
        else:
            selected=(self.DEFAULT_VALUE&mask) if self.HAS_DEFAULT else 0
        if self.REGISTER_OUTPUT:
            result=self.previous
            self.previous=selected
        else:
            result=selected
        return {"out":result}
