class SecureReadWriteBusInterface:
    def __init__(self, p_configurable_key=170, p_data_width=8, p_addr_width=8):
        self.p_configurable_key = p_configurable_key
        self.p_data_width = p_data_width
        self.p_addr_width = p_addr_width
        self.reset()

    def reset(self):
        self.memory=[0]*(1<<self.p_addr_width)
        self.data=0
        self.error=0

    def process(self, i_addr, i_data_in, i_key_in, i_read_write_enable):
        o_data_out=self.data
        o_error=self.error
        self.data=0
        self.error=int(i_key_in!=self.p_configurable_key)
        if not self.error:
            if i_read_write_enable:
                self.data=self.memory[i_addr]
            else:
                self.memory[i_addr]=i_data_in
        return {"o_data_out":o_data_out,"o_error":o_error}
