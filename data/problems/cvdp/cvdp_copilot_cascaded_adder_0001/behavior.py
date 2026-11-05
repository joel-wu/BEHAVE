class CascadedAdder:
    def __init__(self, IN_DATA_WIDTH=16, IN_DATA_NS=4):
        self.IN_DATA_WIDTH = IN_DATA_WIDTH
        self.IN_DATA_NS = IN_DATA_NS
        self.reset()

    def reset(self):
        self.input_data = 0
        self.input_valid = 0
        self.output_data = 0
        self.output_valid = 0

    def process(self, i_valid, i_data):
        old_data = self.output_data
        old_valid = self.output_valid
        total = 0
        for i in range(self.IN_DATA_NS):
            total += (self.input_data >> (i*self.IN_DATA_WIDTH)) & ((1 << self.IN_DATA_WIDTH)-1)
        self.output_data = total
        self.output_valid = self.input_valid
        if i_valid:
            self.input_data = i_data
        self.input_valid = i_valid
        return {"o_valid":old_valid, "o_data":old_data}
