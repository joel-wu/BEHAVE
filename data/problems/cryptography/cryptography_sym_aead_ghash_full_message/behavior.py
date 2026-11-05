def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class GHashFullMessage:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_h,
        in_y0,
        in_len_blocks,
        in_block0,
        in_block1,
        in_block2,
        in_block3,
        in_block4,
        in_block5,
        in_block6,
        in_block7,
        in_block8,
        in_block9,
        in_block10,
        in_block11,
        in_block12,
        in_block13,
        in_block14,
        in_block15,
    ):
        blocks = [
            in_block0,
            in_block1,
            in_block2,
            in_block3,
            in_block4,
            in_block5,
            in_block6,
            in_block7,
            in_block8,
            in_block9,
            in_block10,
            in_block11,
            in_block12,
            in_block13,
            in_block14,
            in_block15,
        ]

        v = [0] * 128
        v[0] = to_unsigned(in_h, 128)
        for i in range(1, 128):
            prev = v[i - 1]
            shifted = prev >> 1
            if prev & 1:
                shifted ^= 0xE1000000000000000000000000000000
            v[i] = to_unsigned(shifted, 128)

        y_cur = to_unsigned(in_y0, 128)
        n = in_len_blocks if in_len_blocks < 16 else 16

        for k in range(16):
            if k < n:
                x = to_unsigned(y_cur ^ blocks[k], 128)
                z = 0
                for i in range(128):
                    bit = (x >> (127 - i)) & 1
                    if bit:
                        z ^= v[i]
                y_cur = to_unsigned(z, 128)

        return {"out_y": to_unsigned(y_cur, 128)}