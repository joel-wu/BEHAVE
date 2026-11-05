class RestoringDivision:
    def __init__(self, WIDTH=6):
        self.WIDTH = WIDTH
        self.reset()

    def reset(self):
        self.busy=0
        self.left=0
        self.bits=0
        self.divisor_q=0
        self.q_work=0
        self.r_work=0
        self.quotient=0
        self.remainder=0
        self.valid=0

    def process(self, start, dividend, divisor):
        quotient=self.quotient
        remainder=self.remainder
        valid=self.valid
        self.valid=0
        mask=(1<<self.WIDTH)-1
        if self.busy:
            shifted=(self.r_work<<1)|(self.bits>>(self.WIDTH-1))
            fits=int(shifted>=self.divisor_q)
            self.r_work=shifted-self.divisor_q if fits else shifted
            self.q_work=((self.q_work<<1)|fits)&mask
            self.bits=(self.bits<<1)&mask
            self.left-=1
            if self.left==0:
                self.quotient=self.q_work
                self.remainder=self.r_work
                self.valid=1
                self.busy=0
        elif start:
            self.busy=1
            self.left=self.WIDTH
            self.bits=dividend
            self.divisor_q=divisor
            self.q_work=0
            self.r_work=0
        return {"quotient":quotient,"remainder":remainder,"valid":valid}
