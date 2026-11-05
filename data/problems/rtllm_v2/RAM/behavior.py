class RAM:
    def __init__(self, WIDTH=6, DEPTH=8):
        self.WIDTH = WIDTH
        self.DEPTH = DEPTH
        self.reset()

    def reset(self):
        self.memory=[0]*self.DEPTH
        self.read_value=0

    def process(self, write_en, write_addr, write_data, read_en, read_addr):
        read_data=self.read_value
        if read_en and read_addr<self.DEPTH:
            self.read_value=self.memory[read_addr]
        else:
            self.read_value=0
        if write_en and write_addr<self.DEPTH:
            self.memory[write_addr]=write_data
        return {"read_data":read_data}
