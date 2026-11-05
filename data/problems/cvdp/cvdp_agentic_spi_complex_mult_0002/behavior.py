class Behavior:
    def __init__(self, IN_WIDTH=16, OUT_WIDTH=32):
        self.IN_WIDTH = IN_WIDTH
        self.OUT_WIDTH = OUT_WIDTH
        self.reset()

    def reset(self):
        self.previous_clock = 0
        self.byte_bits = 0
        self.byte_index = 0
        self.partial = 0
        self.received = 0
        self.result = 0
        self.output_bit = 0

    def process(self, rst_async_n, spi_sck_level, spi_cs_n, spi_mosi):
        if not rst_async_n:
            self.byte_bits = 0
            self.byte_index = 0
            self.partial = 0
            self.received = 0
            self.result = 0
            self.output_bit = 0
        else:
            if spi_cs_n:
                self.byte_bits = 0
                self.partial = 0
            elif spi_sck_level and not self.previous_clock:
                self.partial = ((self.partial << 1) | spi_mosi) & 255
                if self.byte_bits == 7:
                    self.received = ((self.received << 8) | self.partial) & ((1 << (4*self.IN_WIDTH))-1)
                    self.partial = 0
                    self.byte_bits = 0
                    if self.byte_index == self.IN_WIDTH//2-1:
                        mask = (1 << self.IN_WIDTH)-1
                        ar = (self.received >> (3*self.IN_WIDTH)) & mask
                        ai = (self.received >> (2*self.IN_WIDTH)) & mask
                        br = (self.received >> self.IN_WIDTH) & mask
                        bi = self.received & mask
                        if ar >= (1 << (self.IN_WIDTH-1)):
                            ar -= 1 << self.IN_WIDTH
                        if ai >= (1 << (self.IN_WIDTH-1)):
                            ai -= 1 << self.IN_WIDTH
                        if br >= (1 << (self.IN_WIDTH-1)):
                            br -= 1 << self.IN_WIDTH
                        if bi >= (1 << (self.IN_WIDTH-1)):
                            bi -= 1 << self.IN_WIDTH
                        real = (ar*br-ai*bi) & ((1 << self.OUT_WIDTH)-1)
                        imag = (ar*bi+ai*br) & ((1 << self.OUT_WIDTH)-1)
                        self.result = (real << self.OUT_WIDTH) | imag
                        self.byte_index = 0
                        self.received = 0
                    else:
                        self.byte_index += 1
                else:
                    self.byte_bits += 1
            if not spi_sck_level:
                self.output_bit = (self.result >> (4*self.IN_WIDTH-1-8*self.byte_index-self.byte_bits)) & 1
        self.previous_clock = spi_sck_level
        return {"spi_miso": self.output_bit if rst_async_n and not spi_cs_n else 0}
