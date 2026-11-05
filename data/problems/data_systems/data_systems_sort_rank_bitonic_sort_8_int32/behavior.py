def signed32(value):
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


class BitonicSort8Int32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7):
        values = [signed32(in_x0), signed32(in_x1), signed32(in_x2), signed32(in_x3), signed32(in_x4), signed32(in_x5), signed32(in_x6), signed32(in_x7)]
        # Successive bitonic merges alternate direction inside each block.
        for level in range(1, 4):
            block = 1 << level
            for sublevel in range(level):
                stride = 1 << (level - sublevel - 1)
                for i in range(8):
                    partner = i ^ stride
                    if partner > i:
                        ascending = (i & block) == 0
                        if ((values[i] > values[partner]) if ascending
                                else (values[i] < values[partner])):
                            temp = values[i]
                            values[i] = values[partner]
                            values[partner] = temp
        return {
            "out_x0": values[0] & 0xFFFFFFFF,
            "out_x1": values[1] & 0xFFFFFFFF,
            "out_x2": values[2] & 0xFFFFFFFF,
            "out_x3": values[3] & 0xFFFFFFFF,
            "out_x4": values[4] & 0xFFFFFFFF,
            "out_x5": values[5] & 0xFFFFFFFF,
            "out_x6": values[6] & 0xFFFFFFFF,
            "out_x7": values[7] & 0xFFFFFFFF,
        }
