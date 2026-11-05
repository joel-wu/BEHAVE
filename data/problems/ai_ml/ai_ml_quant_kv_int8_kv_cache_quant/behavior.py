def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class KVCacheInt8Quant:
    def __init__(self, head_size=128, slot_bits=16):
        self.head_size = head_size
        self.slot_bits = slot_bits
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, in_slot, in_key, in_value, in_k_scale_num, in_v_scale_num):
        out_key_q = 0
        out_value_q = 0

        for i in range(self.head_size):
            key_i = to_signed((in_key >> (16 * i)) & 0xFFFF, 16)
            value_i = to_signed((in_value >> (16 * i)) & 0xFFFF, 16)

            key_ax = key_i if key_i >= 0 else -key_i
            value_ax = value_i if value_i >= 0 else -value_i

            key_q_mag = 0 if in_k_scale_num == 0 else (2 * key_ax + in_k_scale_num) // (2 * in_k_scale_num)
            value_q_mag = 0 if in_v_scale_num == 0 else (2 * value_ax + in_v_scale_num) // (2 * in_v_scale_num)

            key_q_signed = (key_q_mag if key_i >= 0 else -key_q_mag)
            value_q_signed = (value_q_mag if value_i >= 0 else -value_q_mag)

            key_q_clipped = (-128 if key_q_signed < -128 else key_q_signed)
            key_q_clipped = (127 if key_q_clipped > 127 else key_q_clipped)

            value_q_clipped = (-128 if value_q_signed < -128 else value_q_signed)
            value_q_clipped = (127 if value_q_clipped > 127 else value_q_clipped)

            out_key_q |= to_unsigned(key_q_clipped, 8) << (8 * i)
            out_value_q |= to_unsigned(value_q_clipped, 8) << (8 * i)

        return {
            "out_slot": to_unsigned(in_slot, self.slot_bits),
            "out_key_q": to_unsigned(out_key_q, 8 * self.head_size),
            "out_value_q": to_unsigned(out_value_q, 8 * self.head_size),
        }