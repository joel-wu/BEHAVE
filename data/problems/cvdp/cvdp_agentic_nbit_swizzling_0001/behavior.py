class NbitSwizzling:
    def __init__(self, DATA_WIDTH=64):
        self.DATA_WIDTH=DATA_WIDTH

    def reset(self):
        pass

    def process(self, data_in, sel):
        length=self.DATA_WIDTH>>sel
        result=0
        for i in range(self.DATA_WIDTH):
            source=i+length-1-2*(i%length)
            result|=((data_in>>source)&1)<<i
        return {"data_out":result,"gray_out":result^(result>>1)}
