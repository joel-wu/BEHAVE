class Cipher:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state=0
        self.index=0
        self.left=0
        self.right=0
        self.latched_key=0
        self.output=0
        self.done=0

    def process(self, start, data_in, key):
        data_out=self.output
        done=self.done
        self.done=0
        if self.state==0:
            if start:
                self.left=data_in>>16
                self.right=data_in&65535
                self.latched_key=key
                self.index=0
                self.state=1
        elif self.state==1:
            subkey=(((self.latched_key<<1)|(self.latched_key>>15))&65535)^self.index
            x=self.right^subkey
            x=(((x<<3)|(x>>13))+subkey)&65535
            f=((x<<1)|(x>>15))&65535
            new_right=self.left^f
            self.left=self.right
            self.right=new_right
            if self.index==7:
                self.state=2
            else:
                self.index+=1
        else:
            self.output=(self.right<<16)|self.left
            self.done=1
            self.state=0
        return {"data_out":data_out,"done":done}
