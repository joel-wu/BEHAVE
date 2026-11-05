def to_unsigned(v, b):
    return v & ((1 << b) - 1)


HEAD_TABLES = [
    [
        0x00000000, 0x3F000000, 0x3F800000, 0x3FC00000,
        0x40000000, 0x40200000, 0x40400000, 0x40600000,
        0x40800000, 0x40900000, 0x40A00000, 0x40B00000,
        0x40C00000, 0x40D00000, 0x40E00000, 0x40F00000,
    ],
    [
        0x00000000, 0x3E800000, 0x3F000000, 0x3F400000,
        0x3F800000, 0x3FA00000, 0x3FC00000, 0x3FE00000,
        0x40000000, 0x40100000, 0x40200000, 0x40300000,
        0x40400000, 0x40500000, 0x40600000, 0x40700000,
    ],
    [
        0x00000000, 0x3E000000, 0x3E800000, 0x3EC00000,
        0x3F000000, 0x3F200000, 0x3F400000, 0x3F600000,
        0x3F800000, 0x3F900000, 0x3FA00000, 0x3FB00000,
        0x3FC00000, 0x3FD00000, 0x3FE00000, 0x3FF00000,
    ],
    [
        0x00000000, 0x3D800000, 0x3E000000, 0x3E400000,
        0x3E800000, 0x3EA00000, 0x3EC00000, 0x3EE00000,
        0x3F000000, 0x3F100000, 0x3F200000, 0x3F300000,
        0x3F400000, 0x3F500000, 0x3F600000, 0x3F700000,
    ],
    [
        0x00000000, 0x3D000000, 0x3D800000, 0x3DC00000,
        0x3E000000, 0x3E200000, 0x3E400000, 0x3E600000,
        0x3E800000, 0x3E900000, 0x3EA00000, 0x3EB00000,
        0x3EC00000, 0x3ED00000, 0x3EE00000, 0x3EF00000,
    ],
    [
        0x00000000, 0x3C800000, 0x3D000000, 0x3D400000,
        0x3D800000, 0x3DA00000, 0x3DC00000, 0x3DE00000,
        0x3E000000, 0x3E100000, 0x3E200000, 0x3E300000,
        0x3E400000, 0x3E500000, 0x3E600000, 0x3E700000,
    ],
    [
        0x00000000, 0x3C000000, 0x3C800000, 0x3CC00000,
        0x3D000000, 0x3D200000, 0x3D400000, 0x3D600000,
        0x3D800000, 0x3D900000, 0x3DA00000, 0x3DB00000,
        0x3DC00000, 0x3DD00000, 0x3DE00000, 0x3DF00000,
    ],
    [
        0x00000000, 0x3B800000, 0x3C000000, 0x3C400000,
        0x3C800000, 0x3CA00000, 0x3CC00000, 0x3CE00000,
        0x3D000000, 0x3D100000, 0x3D200000, 0x3D300000,
        0x3D400000, 0x3D500000, 0x3D600000, 0x3D700000,
    ],
]


class AlibiBiasGenerator:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pending = []

    def process(self, in_attention_mask):
        running_count = 0
        position = [0] * 16
        for i in range(16):
            if (in_attention_mask >> i) & 1:
                position[i] = running_count
                running_count += 1
            else:
                position[i] = 0

        outputs = []
        for h in range(8):
            packed = 0
            table = HEAD_TABLES[h]
            for i in range(16):
                word = table[position[i]]
                packed |= to_unsigned(word, 32) << (32 * i)
            outputs.append({"out_head_index": h, "out_alibi": to_unsigned(packed, 512)})
        return outputs