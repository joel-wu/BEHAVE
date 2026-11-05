def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class RecfnToFn:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_recfn):
        sign = (in_recfn >> 32) & 1
        rec_exp = (in_recfn >> 23) & 0x1FF
        rec_sig = in_recfn & 0x7FFFFF

        is_nan = ((rec_exp >> 6) & 0x7) == 0x7
        is_inf = ((rec_exp >> 6) & 0x7) == 0x6
        is_zero = (rec_exp >> 6) == 0

        s_exp = rec_exp
        sig = (0 if is_zero else (1 << 23)) | rec_sig
        is_subnormal = s_exp < 130

        shift = to_unsigned(1 - (s_exp & 0x1F), 5)
        shifted = sig >> 1
        denorm_fract = 0 if shift >= 23 else to_unsigned(shifted >> shift, 23)

        exp_out = 0 if is_subnormal else to_unsigned((s_exp & 0xFF) - 129, 8)
        exp_field = exp_out | (0xFF if (is_nan or is_inf) else 0)

        if is_subnormal:
            fract_field = denorm_fract
        elif is_inf:
            fract_field = 0
        else:
            fract_field = rec_sig

        out_fn = (sign << 31) | (exp_field << 23) | fract_field
        return {"out_fn": to_unsigned(out_fn, 32)}
