class Radix2Divider:
    def __init__(self):
        self.reset()

    def reset(self):
        self.dividend_save = 0
        self.divisor_save = 0
        self.sr = 0
        self.neg_divisor = 0
        self.count = 0
        self.running = 0
        self.valid = 0

    def process(self, dividend, divisor, sign, opn_valid, res_ready):
        remainder = (self.sr >> 8) & 255
        quotient = self.sr & 255
        if sign and (self.dividend_save >> 7):
            remainder = (-remainder) & 255
        if sign and ((self.dividend_save ^ self.divisor_save) >> 7):
            quotient = (-quotient) & 255
        result = (remainder << 8) | quotient
        valid = self.valid
        count = self.count
        if self.running == 0 and opn_valid and valid == 0:
            self.dividend_save = dividend
            self.divisor_save = divisor
            magnitude = dividend
            if sign and (dividend >> 7):
                magnitude = (-dividend) & 255
            self.sr = magnitude << 1
            if sign and (divisor >> 7):
                self.neg_divisor = 256 | divisor
            else:
                self.neg_divisor = (-divisor) & 511
            self.count = 1
            self.running = 1
        elif self.running:
            trial = (self.sr >> 8) + self.neg_divisor
            carry = (trial >> 9) & 1
            selected = self.sr >> 8
            if carry:
                selected = trial & 511
            if count == 8:
                self.sr = ((selected & 255) << 8) | (self.sr & 254) | carry
                self.count = 0
                self.running = 0
            else:
                self.sr = ((selected & 127) << 9) | ((self.sr & 254) << 1) | (carry << 1)
                self.count = count + 1
        if count == 8:
            self.valid = 1
        elif valid and res_ready:
            self.valid = 0
        return {"res_valid": valid, "result": result}
