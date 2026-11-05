class SyncSerialCommunicationTop:
    def __init__(self):
        self.reset()

    def reset(self):
        self.remaining=0
        self.shift=0
        self.received=0
        self.position=0
        self.value=0
        self.done=0
        self.completed=0

    def process(self, data_in, sel):
        data_out=self.value
        done=self.done
        self.done=0
        if self.remaining:
            self.received=self.received | ((self.shift & 1)<<self.position)
            self.shift=self.shift>>1
            self.position+=1
            self.remaining-=1
            if self.remaining==0:
                self.value=self.received
                self.done=1
                self.completed=1
        elif not self.completed:
            width=0
            if sel==1:width=8
            elif sel==2:width=16
            elif sel==3:width=32
            elif sel==4:width=64
            if width:
                self.remaining=width
                self.shift=data_in
                self.received=0
                self.position=0
        return {"data_out":data_out,"done":done}
