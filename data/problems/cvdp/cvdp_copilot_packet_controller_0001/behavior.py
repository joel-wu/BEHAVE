class PacketController:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state=0
        self.count=0
        self.packet=0
        self.checksum=0
        self.response=0
        self.sent=0
        self.tx_start=0
        self.tx_data=0

    def process(self, rx_valid_i, rx_data_8_i, tx_done_tick_i):
        tx_start_o=self.tx_start
        tx_data_8_o=self.tx_data
        if self.state==0:
            if rx_valid_i:
                self.packet=((self.packet<<8)|rx_data_8_i)&0xFFFFFFFFFFFFFFFF
                if self.count==7:
                    self.count=0
                    self.state=1
                else:
                    self.count+=1
        elif self.state==1:
            self.checksum=0
            for i in range(8):
                self.checksum=(self.checksum+((self.packet>>(8*i))&255))&255
            self.state=2 if (self.packet>>48)==0xBACD else 0
        elif self.state==2:
            self.state=3 if self.checksum==0 else 0
        elif self.state==3:
            a=(self.packet>>32)&65535
            b=(self.packet>>16)&65535
            opcode=(self.packet>>8)&255
            result=0
            if opcode==0:
                result=(a+b)&65535
            elif opcode==1:
                result=(a-b)&65535
            high=result>>8
            low=result&255
            check=(-(0xAB+0xCD+high+low))&255
            self.response=(0xABCD<<24)|(result<<8)|check
            self.state=4
        elif self.state==4:
            self.tx_start=1
            self.tx_data=self.response>>32
            self.sent=0
            self.state=5
        elif tx_done_tick_i:
            if self.sent==4:
                self.state=0
                self.tx_start=0
                self.tx_data=0
                self.response=0
            else:
                self.response=(self.response<<8)&0xFFFFFFFFFF
                self.tx_data=self.response>>32
                self.sent+=1
        return {"tx_start_o":tx_start_o,"tx_data_8_o":tx_data_8_o}
