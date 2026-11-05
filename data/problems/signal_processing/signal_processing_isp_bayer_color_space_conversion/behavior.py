"""Per-pixel matrix conversion with the source's two rounding stages."""

# floor(sqrt(2**depth)); odd sensor depths must not use 2**floor(depth/2).
LUMA_OFFSETS = (1, 1, 2, 2, 4, 5, 8, 11, 16, 22, 32, 45, 64, 90, 128, 181,
                256, 362, 512, 724, 1024, 1448, 2048, 2896, 4096, 5792,
                8192, 11585, 16384, 23170, 32768, 46340)
# round(2**(8-depth/2)) for depth < 8, after upscaling to output codes.
LOW_DEPTH_LUMA = (256, 181, 128, 91, 64, 45, 32, 23)


def round_q8(value):
    return (value + 128) // 256 if value >= 0 else -((-value + 128) // 256)


def output_code(value, depth, luma):
    if depth < 8:
        scale = 1 << (8 - depth)
        offset = LOW_DEPTH_LUMA[depth] if luma else 128
        limit = ((1 << depth) - 1) * scale
        return min(255, max(0, min(limit, value * scale + offset)))
    offset = LUMA_OFFSETS[depth] if luma else 1 << (depth - 1)
    clipped = max(0, min((1 << depth) - 1, value + offset))
    shift = depth - 8
    normalized = (clipped + (1 << (shift - 1))) >> shift if shift else clipped
    return min(255, normalized)


class SignalProcessingIspBayerColorSpaceConversion:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_conv_standard, in_bit_depth, in_r, in_g, in_b):
        mask = (1 << min(in_bit_depth, 16)) - 1
        r, g, b = in_r & mask, in_g & mask, in_b & mask
        if in_conv_standard == 1:
            y = 47*r + 157*g + 16*b
            u = -26*r - 86*g + 112*b
            v = 112*r - 102*g - 10*b
        else:
            y = 77*r + 150*g + 29*b
            u = -44*r - 87*g + 138*b
            v = 131*r - 110*g - 21*b
        return dict(out_y=output_code(round_q8(y), in_bit_depth, True),
                    out_u=output_code(round_q8(u), in_bit_depth, False),
                    out_v=output_code(round_q8(v), in_bit_depth, False))
