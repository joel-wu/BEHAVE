def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class DynamicInt8Int8Quantizer:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def _abs16(self, x):
        return -x if x < 0 else x

    def _scale(self, amax):
        if amax == 0:
            return 0
        return (amax * 256 + 126) // 127

    def _quant_one(self, x, scale):
        if scale == 0:
            return 0
        n = x * 256
        if n >= 0:
            q0 = (n + (scale >> 1)) // scale
        else:
            q0 = -(((-n) + (scale >> 1)) // scale)
        if q0 > 127:
            q0 = 127
        elif q0 < -127:
            q0 = -127
        return q0

    def process(
        self,
        in_act_0, in_act_1, in_act_2, in_act_3,
        in_weight_0, in_weight_1, in_weight_2, in_weight_3
    ):
        act = [
            to_signed(in_act_0, 16),
            to_signed(in_act_1, 16),
            to_signed(in_act_2, 16),
            to_signed(in_act_3, 16),
        ]
        weight = [
            to_signed(in_weight_0, 16),
            to_signed(in_weight_1, 16),
            to_signed(in_weight_2, 16),
            to_signed(in_weight_3, 16),
        ]

        act_abs = [self._abs16(x) for x in act]
        weight_abs = [self._abs16(x) for x in weight]

        act_amax = (act_abs[0] if act_abs[0] >= act_abs[1] else act_abs[1])
        act_amax = act_amax if act_amax >= (act_abs[2] if act_abs[2] >= act_abs[3] else act_abs[3]) else (act_abs[2] if act_abs[2] >= act_abs[3] else act_abs[3])

        weight_amax = (weight_abs[0] if weight_abs[0] >= weight_abs[1] else weight_abs[1])
        weight_amax = weight_amax if weight_amax >= (weight_abs[2] if weight_abs[2] >= weight_abs[3] else weight_abs[3]) else (weight_abs[2] if weight_abs[2] >= weight_abs[3] else weight_abs[3])

        act_scale_u8 = self._scale(act_amax)
        weight_scale_u8 = self._scale(weight_amax)

        qact = [self._quant_one(x, act_scale_u8) for x in act]
        qweight = [self._quant_one(x, weight_scale_u8) for x in weight]

        return {
            "out_act_scale": act_scale_u8,
            "out_weight_scale": weight_scale_u8,
            "out_qact_0": to_unsigned(qact[0], 8),
            "out_qact_1": to_unsigned(qact[1], 8),
            "out_qact_2": to_unsigned(qact[2], 8),
            "out_qact_3": to_unsigned(qact[3], 8),
            "out_qweight_0": to_unsigned(qweight[0], 8),
            "out_qweight_1": to_unsigned(qweight[1], 8),
            "out_qweight_2": to_unsigned(qweight[2], 8),
            "out_qweight_3": to_unsigned(qweight[3], 8),
        }
