class ApbGpio:
    def __init__(self, GPIO_WIDTH=8):
        self.GPIO_WIDTH = GPIO_WIDTH
        self.reset()

    def reset(self):
        self.reg_out=0
        self.reg_enable=0
        self.irq_enable=0
        self.irq_type=0
        self.irq_polarity=0
        self.sync1=0
        self.sync2=0
        self.history=0

    def process(self, psel, paddr, penable, pwrite, pwdata, gpio_in):
        mask=(1<<self.GPIO_WIDTH)-1
        rising=self.sync2 & ~self.history & mask
        falling=~self.sync2 & self.history & mask
        edges=(rising & self.irq_polarity)|(falling & ~self.irq_polarity)
        levels=(self.sync2 & self.irq_polarity)|(~self.sync2 & ~self.irq_polarity)
        interrupts=self.irq_enable & ((self.irq_type & edges)|(~self.irq_type & levels)) & mask
        out_value=self.reg_out
        enable_value=self.reg_enable
        read=0
        if psel and penable and not pwrite:
            if paddr==0: read=self.sync2
            elif paddr==1: read=self.reg_out
            elif paddr==2: read=self.reg_enable
            elif paddr==3: read=self.irq_enable
            elif paddr==4: read=self.irq_type
            elif paddr==5: read=self.irq_polarity
            elif paddr==6: read=interrupts
        if psel and penable and pwrite:
            if paddr==1: self.reg_out=pwdata & mask
            elif paddr==2: self.reg_enable=pwdata & mask
            elif paddr==3: self.irq_enable=pwdata & mask
            elif paddr==4: self.irq_type=pwdata & mask
            elif paddr==5: self.irq_polarity=pwdata & mask
        self.history=self.sync2
        self.sync2=self.sync1
        self.sync1=gpio_in
        return {"prdata":read,"pready":1,"pslverr":0,"gpio_out":out_value,"gpio_enable":enable_value,"gpio_int":interrupts,"comb_int":int(interrupts!=0)}
