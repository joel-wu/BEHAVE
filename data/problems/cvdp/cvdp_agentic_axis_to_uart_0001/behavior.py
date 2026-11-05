class AxisToUartTx:
    def __init__(self, CLK_FREQ=100, BIT_RATE=115200, BIT_PER_WORD=8, PARITY_BIT=0, STOP_BITS_NUM=1):
        self.CLK_FREQ = CLK_FREQ
        self.BIT_RATE = BIT_RATE
        self.BIT_PER_WORD = BIT_PER_WORD
        self.PARITY_BIT = PARITY_BIT
        self.STOP_BITS_NUM = STOP_BITS_NUM
        self.reset()

    def reset(self):
        self.frame=0
        self.remaining=0
        self.timer=0

    def process(self, tdata, tvalid):
        tready=int(self.remaining==0)
        TX=(self.frame & 1) if self.remaining else 1
        period=self.CLK_FREQ*1000000//self.BIT_RATE
        if self.remaining==0:
            if tvalid:
                parity=0
                for i in range(self.BIT_PER_WORD):
                    parity ^= (tdata >> i) & 1
                extra=int(self.PARITY_BIT!=0)
                frame=tdata << 1
                if extra:
                    frame |= (parity ^ int(self.PARITY_BIT==1)) << (1+self.BIT_PER_WORD)
                frame |= ((1 << self.STOP_BITS_NUM)-1) << (1+self.BIT_PER_WORD+extra)
                self.frame=frame
                self.remaining=1+self.BIT_PER_WORD+extra+self.STOP_BITS_NUM
                self.timer=period-1
        elif self.timer:
            self.timer-=1
        else:
            self.frame >>= 1
            self.remaining-=1
            self.timer=period-1
        return {"tready":tready,"TX":TX}
