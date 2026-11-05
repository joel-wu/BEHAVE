class SimdMatrixEngineTop:
    def __init__(self, N=8, SIMD_WIDTH=4, DATA_WIDTH=16):
        self.N = N
        self.SIMD_WIDTH = SIMD_WIDTH
        self.DATA_WIDTH = DATA_WIDTH
        self.reset()

    def reset(self):
        self.state=0
        self.row=0
        self.col=0
        self.a=[0]*self.SIMD_WIDTH
        self.b=[0]*self.SIMD_WIDTH
        self.output=0
        self.done=0

    def process(self, start, op_select, mat_a_flat, mat_b_flat):
        mat_result_flat=self.output
        done=self.done
        self.done=0
        mask=(1<<self.DATA_WIDTH)-1
        if self.state==0:
            if start:
                self.row=0
                self.col=0
                self.state=1
        elif self.state==1:
            for i in range(self.SIMD_WIDTH):
                self.a[i]=0
                self.b[i]=0
                if self.col+i<self.N:
                    shift=(self.row*self.N+self.col+i)*self.DATA_WIDTH
                    self.a[i]=(mat_a_flat>>shift)&mask
                    self.b[i]=(mat_b_flat>>shift)&mask
            self.state=2
        elif self.state==2:
            for i in range(self.SIMD_WIDTH):
                if self.col+i<self.N:
                    if op_select==0:
                        value=(self.a[i]+self.b[i])&mask
                    elif op_select==1:
                        value=(self.a[i]-self.b[i])&mask
                    elif op_select==2:
                        value=(self.a[i]*self.b[i])&mask
                    else:
                        value=0
                    shift=(self.row*self.N+self.col+i)*self.DATA_WIDTH
                    self.output=(self.output&~(mask<<shift))|(value<<shift)
            if self.col+self.SIMD_WIDTH<self.N:
                self.col+=self.SIMD_WIDTH
                self.state=1
            elif self.row+1<self.N:
                self.row+=1
                self.col=0
                self.state=1
            else:
                self.state=3
        else:
            self.done=1
            self.state=0
        return {"mat_result_flat":mat_result_flat,"done":done}
