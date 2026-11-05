class GcdTop:
    def __init__(self, WIDTH=4):
        self.WIDTH = WIDTH
        self.reset()

    def reset(self):
        self.a=0
        self.b=0
        self.state=0
        self.result=0
        self.done=0

    def process(self, A, B, go):
        result=self.result
        done=self.done
        self.done=0
        if self.state==1:
            self.result=self.a
            self.done=1
            self.state=0
        elif self.state!=0 or go:
            if self.state==0:
                self.a=B if A==0 else A
                self.b=A if B==0 else B
            elif self.state==2:
                self.a-=self.b
            else:
                self.b-=self.a
            self.state=1 if self.a==self.b else 2 if self.a>self.b else 3
        return {"OUT":result,"done":done}
