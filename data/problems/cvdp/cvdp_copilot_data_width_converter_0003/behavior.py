class DataWidthConverter:
    def __init__(self):
        self.reset()

    def reset(self):
        self.partial = 0
        self.count = 0
        self.output_data = 0
        self.output_valid = 0

    def process(self, data_valid, data_in):
        old_data = self.output_data
        old_valid = self.output_valid
        self.output_valid = 0
        if data_valid:
            word = (self.partial << 32) | data_in
            if self.count == 3:
                self.output_data = word
                self.output_valid = 1
                self.partial = 0
                self.count = 0
            else:
                self.partial = word
                self.count += 1
        return {"o_data_out_valid":old_valid, "o_data_out":old_data}
