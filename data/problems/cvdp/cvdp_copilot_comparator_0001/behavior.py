class SignedComparator:
    def __init__(self, WIDTH=5):
        self.WIDTH = WIDTH

    def reset(self):
        pass

    def process(self, i_A, i_B, i_enable, i_mode):
        a = i_A
        b = i_B
        if i_mode:
            if a & (1 << (self.WIDTH - 1)):
                a -= 1 << self.WIDTH
            if b & (1 << (self.WIDTH - 1)):
                b -= 1 << self.WIDTH
        return {"o_greater": 1 if i_enable and a > b else 0,
                "o_less": 1 if i_enable and a < b else 0,
                "o_equal": 1 if i_enable and a == b else 0}
