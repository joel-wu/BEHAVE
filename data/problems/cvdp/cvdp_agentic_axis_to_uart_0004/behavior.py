class UartRxToAxis:
    def __init__(self, CLK_FREQ=100, BIT_RATE=115200, BIT_PER_WORD=8, PARITY_BIT=0, STOP_BITS_NUM=1):
        self.CLK_FREQ = CLK_FREQ
        self.BIT_RATE = BIT_RATE
        self.BIT_PER_WORD = BIT_PER_WORD
        self.PARITY_BIT = PARITY_BIT
        self.STOP_BITS_NUM = STOP_BITS_NUM
        self.reset()

    def reset(self):
        self.phase=0
        self.previous=1
        self.timer=0
        self.index=0
        self.word=0
        self.parity=0
        self.bad_parity=0
        self.bad_stop=0
        self.stops=0
        self.data=0
        self.error=0

    def process(self, RX):
        tdata=self.data
        tuser=self.error
        tvalid=int(self.phase==6)
        period=self.CLK_FREQ*1000000//self.BIT_RATE
        if self.phase==0:
            if self.previous and not RX:
                self.phase=1
                self.timer=period//2-1
                self.word=0
                self.index=0
                self.parity=0
                self.bad_parity=0
                self.bad_stop=0
                self.stops=0
        elif self.phase==6:
            self.phase=0
        elif self.timer:
            self.timer-=1
        else:
            self.timer=period-1
            if self.phase==1:
                self.phase=0 if RX else 2
            elif self.phase==2:
                self.word |= RX << self.index
                self.parity ^= RX
                if self.index==self.BIT_PER_WORD-1:
                    self.phase=3 if self.PARITY_BIT else 4
                else:
                    self.index+=1
            elif self.phase==3:
                self.bad_parity=int(RX != (self.parity ^ int(self.PARITY_BIT==1)))
                self.phase=4
            elif self.phase==4:
                self.bad_stop |= int(not RX)
                self.stops+=1
                if self.stops==self.STOP_BITS_NUM:
                    if not self.bad_stop:
                        self.data=self.word
                        self.error=self.bad_parity
                        self.phase=6
                    else:
                        self.phase=0
        self.previous=RX
        return {"tdata":tdata,"tuser":tuser,"tvalid":tvalid}
