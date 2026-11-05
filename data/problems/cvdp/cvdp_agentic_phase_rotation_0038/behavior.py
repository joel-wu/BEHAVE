class ControlFsm:
    def __init__(self, NBW_WAIT=32):
        self.NBW_WAIT = NBW_WAIT
        self.reset()

    def reset(self):
        self.phase=0
        self.remaining=0
        self.saved_wait=0
        self.timeout_count=0
        self.subsampling=0

    def process(self, i_enable, i_subsampling, i_valid, i_calc_valid, i_calc_fail, i_wait):
        o_subsampling=self.subsampling
        o_valid=int(self.phase==1 and i_valid and self.remaining!=0)
        o_start_calc=int(self.phase==2 and self.remaining==16)
        if self.phase==0:
            if i_enable:
                self.subsampling=i_subsampling
                self.saved_wait=i_wait
                self.remaining=256 if i_subsampling else 8
                self.phase=1
        elif self.phase==1:
            if i_valid:
                if self.remaining==1:
                    self.remaining=16
                    self.phase=2
                else:
                    self.remaining-=1
        elif self.phase==2:
            if self.remaining==1:
                self.remaining=0
                self.phase=3
            else:
                self.remaining-=1
        elif self.phase==3:
            if i_calc_fail:
                self.phase=0
            elif i_calc_valid:
                self.timeout_count=self.saved_wait
                self.phase=4
        else:
            if not i_enable or self.timeout_count==0:
                self.phase=0
            else:
                self.timeout_count-=1
        return {"o_subsampling":o_subsampling,"o_valid":o_valid,"o_start_calc":o_start_calc}
