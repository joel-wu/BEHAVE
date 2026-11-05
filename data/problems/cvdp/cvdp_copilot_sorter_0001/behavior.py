class SortingEngine:
    def __init__(self, N=8, WIDTH=8):
        self.N = N
        self.WIDTH = WIDTH
        self.reset()

    def reset(self):
        self.work=[0]*self.N
        self.state=0
        self.left=0
        self.j=0
        self.done=0
        self.out_data=0

    def process(self, start, in_data):
        done=self.done
        out_data=self.out_data
        self.done=0
        if self.state==0:
            if start:
                for i in range(self.N):
                    self.work[i]=(in_data>>(i*self.WIDTH))&((1<<self.WIDTH)-1)
                self.j=0
                self.left=self.N*(self.N-1)
                self.state=2 if self.N==1 else 1
        elif self.state==1:
            a=self.work[self.j]
            b=self.work[self.j+1]
            if a>b:
                self.work[self.j]=b
                self.work[self.j+1]=a
            self.j=0 if self.j==self.N-2 else self.j+1
            self.left-=1
            if self.left==0:
                self.state=2
        else:
            packed=0
            for i in range(self.N):
                packed|=self.work[i]<<(i*self.WIDTH)
            self.out_data=packed
            self.done=1
            self.state=0
        return {"done":done,"out_data":out_data}
