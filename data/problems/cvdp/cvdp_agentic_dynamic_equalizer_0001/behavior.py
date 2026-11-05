class DynamicEqualizer:
    def __init__(self, TAP_NUM=7, DATA_WIDTH=16, COEFF_WIDTH=16, MU=15):
        self.TAP_NUM = TAP_NUM
        self.DATA_WIDTH = DATA_WIDTH
        self.COEFF_WIDTH = COEFF_WIDTH
        self.MU = MU
        self.reset()

    def reset(self):
        self.x_real = [0] * self.TAP_NUM
        self.x_imag = [0] * self.TAP_NUM
        self.w_real = [0] * self.TAP_NUM
        self.w_real[self.TAP_NUM // 2] = 8192
        self.w_imag = [0] * self.TAP_NUM
        self.y_real = 0
        self.y_imag = 0

    def process(self, data_in_real, data_in_imag, desired_real, desired_imag):
        out = {"data_out_real": self.y_real, "data_out_imag": self.y_imag}
        ar = data_in_real
        ai = data_in_imag
        dr = desired_real
        di = desired_imag
        for k in range(self.TAP_NUM-1, 0, -1):
            self.x_real[k] = self.x_real[k-1]
            self.x_imag[k] = self.x_imag[k-1]
        self.x_real[0] = ar
        self.x_imag[0] = ai
        yr = 0
        yi = 0
        for k in range(self.TAP_NUM):
            yr += (self.w_real[k]*self.x_real[k] - self.w_imag[k]*self.x_imag[k]) >> 13
            yi += (self.w_real[k]*self.x_imag[k] + self.w_imag[k]*self.x_real[k]) >> 13
        yr = min(32767, max(-32768, yr))
        yi = min(32767, max(-32768, yi))
        er = min(32767, max(-32768, dr-yr))
        ei = min(32767, max(-32768, di-yi))
        for k in range(self.TAP_NUM):
            nr = self.w_real[k] + ((er*self.x_real[k] + ei*self.x_imag[k]) >> (13+self.MU))
            ni = self.w_imag[k] + ((ei*self.x_real[k] - er*self.x_imag[k]) >> (13+self.MU))
            self.w_real[k] = min(32767, max(-32768, nr))
            self.w_imag[k] = min(32767, max(-32768, ni))
        self.y_real = yr
        self.y_imag = yi
        return out
