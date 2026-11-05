class DataBusController:
    def __init__(self, AFINITY=0):
        self.AFINITY = AFINITY
        self.reset()

    def reset(self):
        self.pending=0
        self.data=0

    def process(self, m0_valid, m0_data, m1_valid, m1_data, s_ready):
        selected=2
        if m0_valid and m1_valid:
            selected=self.AFINITY
        elif m0_valid:
            selected=0
        elif m1_valid:
            selected=1
        m0_ready=int(s_ready and selected==0)
        m1_ready=int(s_ready and selected==1)
        s_valid=self.pending
        s_data=self.data if self.pending else 0
        if s_ready:
            self.pending=int(selected!=2)
            if selected==0:
                self.data=m0_data
            elif selected==1:
                self.data=m1_data
            else:
                self.data=0
        return {"m0_ready":m0_ready,"m1_ready":m1_ready,"s_valid":s_valid,"s_data":s_data}
