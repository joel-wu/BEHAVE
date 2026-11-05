class EnhancedFsmSignalProcessor:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0
        self.result = 0

    def process(self, i_enable, i_clear, i_ack, i_fault,
                i_vector_1, i_vector_2, i_vector_3, i_vector_4, i_vector_5, i_vector_6):
        output = {"o_ready":int(self.state==2), "o_error":int(self.state==3),
                  "o_fsm_status":self.state,
                  "o_vector_1":(self.result>>24)&255, "o_vector_2":(self.result>>16)&255,
                  "o_vector_3":(self.result>>8)&255, "o_vector_4":self.result&255}
        if i_fault:
            self.state = 3
            self.result = 0
        elif self.state == 0:
            if i_enable:
                self.state = 1
        elif self.state == 1:
            self.result = ((i_vector_1&31)<<27)|((i_vector_2&31)<<22)|((i_vector_3&31)<<17)|((i_vector_4&31)<<12)|((i_vector_5&31)<<7)|((i_vector_6&31)<<2)|3
            self.state = 2
        elif (self.state==2 and i_ack) or (self.state==3 and i_clear):
            self.state = 0
            self.result = 0
        return output
