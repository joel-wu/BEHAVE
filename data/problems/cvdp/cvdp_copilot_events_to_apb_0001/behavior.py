class ApbController:
    def __init__(self):
        self.reset()

    def reset(self):
        self.phase=0
        self.saved_addr=0
        self.saved_data=0
        self.wait_count=0

    def process(self, select_a_i, select_b_i, select_c_i, addr_a_i, data_a_i, addr_b_i, data_b_i, addr_c_i, data_c_i, apb_pready_i):
        active=int(self.phase!=0)
        access=int(self.phase==2)
        address=self.saved_addr if active else 0
        data=self.saved_data if active else 0
        if self.phase==0:
            if select_a_i or select_b_i or select_c_i:
                self.saved_addr=addr_a_i if select_a_i else (addr_b_i if select_b_i else addr_c_i)
                self.saved_data=data_a_i if select_a_i else (data_b_i if select_b_i else data_c_i)
                self.phase=1
                self.wait_count=0
        elif self.phase==1:
            self.phase=2
        else:
            if apb_pready_i or self.wait_count==14:
                self.phase=0
                self.wait_count=0
            else:
                self.wait_count+=1
        return {"apb_psel_o":active,"apb_penable_o":access,"apb_pwrite_o":active,"apb_paddr_o":address,"apb_pwdata_o":data}
