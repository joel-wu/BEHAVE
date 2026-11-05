DEFAULT = (0, 0, 0, 0, 59097, 6439, 0, 0, 13068, 52468, 0, 0, 0, 45623, 19913, 0, 0, 17816, 47720, 0, 0, 0, 59501, 6035, 0, 0, 42702, 22834, 0, 0, 25904, 39632, 0, 0, 9105, 56431, 0, 0, 0, 60889, 0, 0, 0, 50740, 0, 0, 0, 40592, 0, 0, 0, 30444, 0, 0, 0, 20296, 0, 0, 0, 10148, 0, 0, 0, 0)


def signed32(word):
    return word-4294967296 if word >= 2147483648 else word


class MelFilterbankApply:
    def __init__(self):
        self.reset()

    def reset(self):
        self.fb = [DEFAULT[i] for i in range(64)]
        self.freq_index = 0
        self.partial_sum = [0]*4

    def process(self, in_spectrum, cfg_valid, cfg_we, cfg_addr, cfg_wdata):
        # Configuration is part of this accepted input, not an independent clock.
        if cfg_valid and cfg_we:
            self.fb[cfg_addr] = cfg_wdata
        x = signed32(in_spectrum)
        for m in range(4):
            self.partial_sum[m] += x*signed32(self.fb[self.freq_index*4+m])
        if self.freq_index == 15:
            output = []
            for m in range(4):
                output.append({"out_mel": self.partial_sum[m] & 0xffffffffffffffff})
            self.freq_index = 0
            self.partial_sum = [0]*4
            return output
        self.freq_index += 1
        return None
