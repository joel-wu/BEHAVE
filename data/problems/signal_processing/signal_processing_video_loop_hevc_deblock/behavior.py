def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class HevcDeblockBoundaryStrength:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_block_strength_pre,
        in_p_intra,
        in_q_intra,
        in_p_cbf_luma,
        in_q_cbf_luma,
        in_slice_p_is_inter_p,
        in_slice_q_is_inter_p,
        in_ref_p0,
        in_ref_q0,
        in_ref_p1,
        in_ref_q1,
        in_mv_p0_x,
        in_mv_p0_y,
        in_mv_q0_x,
        in_mv_q0_y,
        in_mv_p1_x,
        in_mv_p1_y,
        in_mv_q1_x,
        in_mv_q1_y,
    ):
        ref_p0 = to_unsigned(in_ref_p0, 8)
        ref_q0 = to_unsigned(in_ref_q0, 8)
        ref_p1 = to_unsigned(in_ref_p1, 8)
        ref_q1 = to_unsigned(in_ref_q1, 8)

        mv_p0_x = 0 if ref_p0 == 0xFF else to_signed(in_mv_p0_x, 16)
        mv_p0_y = 0 if ref_p0 == 0xFF else to_signed(in_mv_p0_y, 16)
        mv_q0_x = 0 if ref_q0 == 0xFF else to_signed(in_mv_q0_x, 16)
        mv_q0_y = 0 if ref_q0 == 0xFF else to_signed(in_mv_q0_y, 16)
        mv_p1_x = 0 if ref_p1 == 0xFF else to_signed(in_mv_p1_x, 16)
        mv_p1_y = 0 if ref_p1 == 0xFF else to_signed(in_mv_p1_y, 16)
        mv_q1_x = 0 if ref_q1 == 0xFF else to_signed(in_mv_q1_x, 16)
        mv_q1_y = 0 if ref_q1 == 0xFF else to_signed(in_mv_q1_y, 16)

        if in_p_intra or in_q_intra:
            bs = 2
        elif (in_block_strength_pre & 3) > 1 and (in_p_cbf_luma or in_q_cbf_luma):
            bs = 1
        elif in_slice_p_is_inter_p and in_slice_q_is_inter_p:
            dx = mv_q0_x - mv_p0_x
            dy = mv_q0_y - mv_p0_y
            bs = 1 if (ref_p0 != ref_q0) or (dx <= -4 or dx >= 4) or (dy <= -4 or dy >= 4) else 0
        else:
            same_order = (ref_p0 == ref_q0) and (ref_p1 == ref_q1)
            cross_order = (ref_p0 == ref_q1) and (ref_p1 == ref_q0)
            if not (same_order or cross_order):
                bs = 1
            elif ref_p0 != ref_p1:
                if ref_p0 == ref_q0:
                    d00x = mv_q0_x - mv_p0_x
                    d00y = mv_q0_y - mv_p0_y
                    d11x = mv_q1_x - mv_p1_x
                    d11y = mv_q1_y - mv_p1_y
                    bs = 1 if (d00x <= -4 or d00x >= 4 or d00y <= -4 or d00y >= 4 or d11x <= -4 or d11x >= 4 or d11y <= -4 or d11y >= 4) else 0
                else:
                    d10x = mv_q1_x - mv_p0_x
                    d10y = mv_q1_y - mv_p0_y
                    d01x = mv_q0_x - mv_p1_x
                    d01y = mv_q0_y - mv_p1_y
                    bs = 1 if (d10x <= -4 or d10x >= 4 or d10y <= -4 or d10y >= 4 or d01x <= -4 or d01x >= 4 or d01y <= -4 or d01y >= 4) else 0
            else:
                d00x = mv_q0_x - mv_p0_x
                d00y = mv_q0_y - mv_p0_y
                d11x = mv_q1_x - mv_p1_x
                d11y = mv_q1_y - mv_p1_y
                d10x = mv_q1_x - mv_p0_x
                d10y = mv_q1_y - mv_p0_y
                d01x = mv_q0_x - mv_p1_x
                d01y = mv_q0_y - mv_p1_y
                cond_a = d00x <= -4 or d00x >= 4 or d00y <= -4 or d00y >= 4 or d11x <= -4 or d11x >= 4 or d11y <= -4 or d11y >= 4
                cond_b = d10x <= -4 or d10x >= 4 or d10y <= -4 or d10y >= 4 or d01x <= -4 or d01x >= 4 or d01y <= -4 or d01y >= 4
                bs = 1 if cond_a and cond_b else 0

        return {"out_bs": to_unsigned(bs, 2)}
