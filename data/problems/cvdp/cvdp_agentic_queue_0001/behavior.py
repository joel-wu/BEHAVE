class Queue:
    def __init__(self, DEPTH=4, DBITS=8, ALMOST_EMPTY_THRESHOLD=1, ALMOST_FULL_THRESHOLD=1):
        self.DEPTH = DEPTH
        self.DBITS = DBITS
        self.ALMOST_EMPTY_THRESHOLD = ALMOST_EMPTY_THRESHOLD
        self.ALMOST_FULL_THRESHOLD = ALMOST_FULL_THRESHOLD
        self.reset()

    def reset(self):
        self.data=[0]*self.DEPTH
        self.count=0

    def process(self, clr_i, ena_i, we_i, re_i, d_i):
        q=self.data[0] if self.count else 0
        empty=int(self.count==0)
        full=int(self.count==self.DEPTH)
        ae=min(self.DEPTH,self.ALMOST_EMPTY_THRESHOLD)
        af=max(0,self.DEPTH-self.ALMOST_FULL_THRESHOLD)
        almost_empty=int(self.count<=ae)
        almost_full=int(self.count>=af)
        if clr_i:
            for i in range(self.DEPTH):
                self.data[i]=0
            self.count=0
        elif ena_i:
            if re_i and self.count:
                for i in range(self.DEPTH-1):
                    self.data[i]=self.data[i+1]
                self.data[self.DEPTH-1]=0
                self.count-=1
            if we_i and self.count<self.DEPTH:
                self.data[self.count]=d_i
                self.count+=1
        return {"q_o":q,"empty_o":empty,"full_o":full,"almost_empty_o":almost_empty,"almost_full_o":almost_full}
