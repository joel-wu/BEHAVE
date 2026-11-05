class SecureReadWriteRegisterBank:
    def __init__(self, p_address_width=8, p_data_width=8, p_unlock_code_0=171, p_unlock_code_1=205):
        self.p_address_width = p_address_width
        self.p_data_width = p_data_width
        self.p_unlock_code_0 = p_unlock_code_0
        self.p_unlock_code_1 = p_unlock_code_1
        self.reset()

    def reset(self):
        self.memory=[0]*(1<<self.p_address_width)
        self.unlocked=0
        self.armed=0
        self.data=0

    def process(self, i_addr, i_data_in, i_read_write_enable):
        o_data_out=self.data
        self.data=0
        if not i_read_write_enable and i_addr==0:
            if i_data_in!=self.p_unlock_code_0:
                self.unlocked=0
        elif not i_read_write_enable and i_addr==1:
            if i_data_in!=self.p_unlock_code_1:
                self.unlocked=0
            elif self.armed:
                self.unlocked=1
        elif self.unlocked and i_addr>=2:
            if i_read_write_enable:
                self.data=self.memory[i_addr]
            else:
                self.memory[i_addr]=i_data_in
        self.armed=int(not i_read_write_enable and i_addr==0 and i_data_in==self.p_unlock_code_0)
        return {"o_data_out":o_data_out}
