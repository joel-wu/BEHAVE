class FlacRiceDecode:
    def __init__(self):
        self.reset()

    def reset(self):
        self.active = 0
        self.parameter = 0
        self.quotient = 0
        self.remainder = 0
        self.remaining = 0
        self.failed = 0

    def process(self, in_byte, in_nbits, in_last, in_parameter):
        outputs = []
        if not self.active:
            self.active = 1
            self.parameter = in_parameter & 31
        nbits = in_nbits & 15
        if not self.failed and (nbits > 8 or (not in_last and nbits != 8)):
            self.failed = 1
            outputs.append({"out_value": 0, "out_success": 0})
        for index in range(8):
            if index < nbits and not self.failed:
                bit = (in_byte >> (7 - index)) & 1
                completed = 0
                if self.remaining:
                    self.remainder = (self.remainder << 1) | bit
                    self.remaining -= 1
                    if self.remaining == 0:
                        completed = 1
                elif not bit:
                    if self.quotient == (0xFFFFFFFF >> self.parameter):
                        self.failed = 1
                        outputs.append({"out_value": 0, "out_success": 0})
                    else:
                        self.quotient += 1
                elif self.parameter:
                    self.remaining = self.parameter
                    self.remainder = 0
                else:
                    completed = 1
                if completed:
                    folded = (self.quotient << self.parameter) | self.remainder
                    value = (folded >> 1) ^ (0xFFFFFFFF if folded & 1 else 0)
                    outputs.append({"out_value": value, "out_success": 1})
                    self.quotient = 0
                    self.remainder = 0
        if in_last:
            if not self.failed and (self.quotient or self.remaining):
                outputs.append({"out_value": 0, "out_success": 0})
            self.reset()
        return outputs
