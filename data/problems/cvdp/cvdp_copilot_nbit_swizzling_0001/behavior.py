class NbitSwizzling:
    def __init__(self, DATA_WIDTH=64):
        self.DATA_WIDTH = DATA_WIDTH

    def reset(self):
        pass

    def process(self, data_in, sel):
        result = 0
        section_width = self.DATA_WIDTH >> sel
        for i in range(self.DATA_WIDTH):
            section_base = (i // section_width) * section_width
            source_bit = section_base + section_width - 1 - (i % section_width)
            result |= ((data_in >> source_bit) & 1) << i
        return {"data_out": result}
