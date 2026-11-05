class EthernetPacketParser:
    def __init__(self):
        self.reset()

    def reset(self):
        self.active=0
        self.count=0
        self.pending=0
        self.extracted=0
        self.field=0
        self.valid=0

    def process(self, vld, sof, data, eof):
        field=self.field
        field_vld=self.valid
        if self.pending:
            self.field=self.extracted
            self.valid=1
            self.pending=0
        if vld:
            if sof:
                self.active=1
                self.count=1
                self.field=0
                self.valid=0
                self.pending=0
            elif self.active:
                if self.count==1:
                    self.extracted=data>>16
                    self.pending=1
                if self.count<15:
                    self.count+=1
            if eof:
                self.active=0
                self.count=0
                self.pending=0
                self.valid=0
        return {"ack":1,"field":field,"field_vld":field_vld}
