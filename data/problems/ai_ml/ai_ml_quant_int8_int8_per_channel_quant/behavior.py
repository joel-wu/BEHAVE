from fp_lib import fp32_div, fp32_mul, fp32_cmp


def signed8(word):
    value = word & 255
    return value - 256 if value & 128 else value


def round_integer(word):
    exponent = (word >> 23) & 255
    if exponent < 126:
        return 0
    # Called only for finite values with magnitude less than 256.
    shift = 150 - exponent
    mantissa = (word & 0x7FFFFF) | 0x800000
    integer = mantissa >> shift
    remainder = mantissa & ((1 << shift) - 1)
    halfway = 1 << (shift - 1)
    if remainder > halfway or (remainder == halfway and integer & 1):
        integer = integer + 1
    return -integer if word >> 31 else integer


class Int8PerChannelQuant:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_input, in_scale, in_zero_point, in_quant_min, in_quant_max):
        qmin = signed8(in_quant_min)
        qmax = signed8(in_quant_max)
        bad = qmin > qmax
        packed = 0
        for channel in range(2):
            scale = (in_scale >> (32 * channel)) & 0xFFFFFFFF
            zero = signed8(in_zero_point >> (8 * channel))
            valid_scale = not (scale >> 31) and 0 < ((scale >> 23) & 255) < 255
            if not valid_scale:
                bad = True
            inverse = fp32_div(0x3F800000, scale)
            for sample in range(2):
                lane = 2 * channel + sample
                value = (in_input >> (32 * lane)) & 0xFFFFFFFF
                if ((value >> 23) & 255) == 255:
                    bad = True
                scaled = fp32_mul(value, inverse)
                if (scaled & 0x7FFFFFFF) > 0x7F800000:
                    bad = True
                    quant = 0
                elif fp32_cmp(scaled, 0x43800000) >= 0:
                    quant = qmax
                elif fp32_cmp(scaled, 0xC3800000) <= 0:
                    quant = qmin
                else:
                    quant = round_integer(scaled) + zero
                    quant = min(qmax, max(qmin, quant))
                packed = packed | ((quant & 255) << (8 * lane))
        return {"out_quantized": 0 if bad else packed, "out_error": 1 if bad else 0}
