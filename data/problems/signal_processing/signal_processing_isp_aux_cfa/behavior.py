"""Malvar demosaicing of a two-by-two Bayer cell with its complete halo."""
PATTERN_TYPES = ((0, 1, 2, 3), (3, 2, 1, 0), (2, 3, 0, 1), (1, 0, 3, 2))


class SignalProcessingIspAuxCfa:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def _pixel(self, p, y, x, kind, index, limit):
        c = p[y][x]
        far_v = p[y-2][x] + p[y+2][x]
        far_h = p[y][x-2] + p[y][x+2]
        diagonal = p[y-1][x-1] + p[y-1][x+1] + p[y+1][x-1] + p[y+1][x+1]
        near_v = p[y-1][x] + p[y+1][x]
        near_h = p[y][x-1] + p[y][x+1]
        # All numerators have denominator 16; no intermediate rounding.
        green = 8*c - 2*(far_v+far_h) + 4*(near_v+near_h)
        opposite = 12*c - 3*(far_v+far_h) + 4*diagonal
        horizontal = 10*c - 2*far_h - 2*diagonal + far_v + 8*near_h
        vertical = 10*c - 2*far_v - 2*diagonal + far_h + 8*near_v
        if kind == 0:
            red, green, blue = 16*c, green, opposite
        elif kind == 1:
            red, green, blue = horizontal, 16*c, vertical
        elif kind == 2:
            red, green, blue = vertical, 16*c, horizontal
        else:
            red, green, blue = opposite, green, 16*c
        return {"out_pixel_index": index,
                "out_r": min(limit, max(0, red//16)),
                "out_g": min(limit, max(0, green//16)),
                "out_b": min(limit, max(0, blue//16))}

    def process(self, in_bayer_pattern, in_clip,
        in_p00, in_p01, in_p02, in_p03, in_p04, in_p05,
        in_p10, in_p11, in_p12, in_p13, in_p14, in_p15,
        in_p20, in_p21, in_p22, in_p23, in_p24, in_p25,
        in_p30, in_p31, in_p32, in_p33, in_p34, in_p35,
        in_p40, in_p41, in_p42, in_p43, in_p44, in_p45,
        in_p50, in_p51, in_p52, in_p53, in_p54, in_p55):
        p = [
            [in_p00, in_p01, in_p02, in_p03, in_p04, in_p05],
            [in_p10, in_p11, in_p12, in_p13, in_p14, in_p15],
            [in_p20, in_p21, in_p22, in_p23, in_p24, in_p25],
            [in_p30, in_p31, in_p32, in_p33, in_p34, in_p35],
            [in_p40, in_p41, in_p42, in_p43, in_p44, in_p45],
            [in_p50, in_p51, in_p52, in_p53, in_p54, in_p55],
        ]
        kinds = PATTERN_TYPES[in_bayer_pattern]
        return [self._pixel(p, 2, 2, kinds[0], 0, in_clip),
                self._pixel(p, 2, 3, kinds[1], 1, in_clip),
                self._pixel(p, 3, 2, kinds[2], 2, in_clip),
                self._pixel(p, 3, 3, kinds[3], 3, in_clip)]
