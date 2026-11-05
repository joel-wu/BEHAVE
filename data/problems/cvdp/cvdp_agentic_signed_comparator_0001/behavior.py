class SignedComparator:
    def __init__(self, DATA_WIDTH=16, REGISTER_OUTPUT=0, ENABLE_TOLERANCE=0, TOLERANCE=0, SHIFT_LEFT=0):
        self.DATA_WIDTH = DATA_WIDTH
        self.REGISTER_OUTPUT = REGISTER_OUTPUT
        self.ENABLE_TOLERANCE = ENABLE_TOLERANCE
        self.TOLERANCE = TOLERANCE
        self.SHIFT_LEFT = SHIFT_LEFT
        self.reset()

    def reset(self):
        self.previous=0

    def process(self, enable, bypass, a, b):
        mask=(1<<self.DATA_WIDTH)-1
        sign=1<<(self.DATA_WIDTH-1)
        left=(a<<self.SHIFT_LEFT)&mask
        right=(b<<self.SHIFT_LEFT)&mask
        if left&sign:
            left-=1<<self.DATA_WIDTH
        if right&sign:
            right-=1<<self.DATA_WIDTH
        distance=left-right
        if distance<0:
            distance=-distance
        if bypass:
            relation=1
        elif not enable:
            relation=0
        elif self.ENABLE_TOLERANCE and distance<=self.TOLERANCE:
            relation=1
        elif left>right:
            relation=4
        elif left<right:
            relation=2
        else:
            relation=1
        if self.REGISTER_OUTPUT:
            result=self.previous
            self.previous=relation
        else:
            result=relation
        return {"gt":(result>>2)&1,"lt":(result>>1)&1,"eq":result&1}
