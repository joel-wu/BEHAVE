def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class AiMlVisionDwconvConvnextDw7x7:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_x00, in_x01, in_x02, in_x03, in_x04, in_x05, in_x06,
        in_x10, in_x11, in_x12, in_x13, in_x14, in_x15, in_x16,
        in_x20, in_x21, in_x22, in_x23, in_x24, in_x25, in_x26,
        in_x30, in_x31, in_x32, in_x33, in_x34, in_x35, in_x36,
        in_x40, in_x41, in_x42, in_x43, in_x44, in_x45, in_x46,
        in_x50, in_x51, in_x52, in_x53, in_x54, in_x55, in_x56,
        in_x60, in_x61, in_x62, in_x63, in_x64, in_x65, in_x66,
        in_w00, in_w01, in_w02, in_w03, in_w04, in_w05, in_w06,
        in_w10, in_w11, in_w12, in_w13, in_w14, in_w15, in_w16,
        in_w20, in_w21, in_w22, in_w23, in_w24, in_w25, in_w26,
        in_w30, in_w31, in_w32, in_w33, in_w34, in_w35, in_w36,
        in_w40, in_w41, in_w42, in_w43, in_w44, in_w45, in_w46,
        in_w50, in_w51, in_w52, in_w53, in_w54, in_w55, in_w56,
        in_w60, in_w61, in_w62, in_w63, in_w64, in_w65, in_w66,
        in_bias
    ):
        partial_sum = to_signed(in_bias, 32)

        partial_sum += to_signed(in_x00, 16) * to_signed(in_w00, 16)
        partial_sum += to_signed(in_x01, 16) * to_signed(in_w01, 16)
        partial_sum += to_signed(in_x02, 16) * to_signed(in_w02, 16)
        partial_sum += to_signed(in_x03, 16) * to_signed(in_w03, 16)
        partial_sum += to_signed(in_x04, 16) * to_signed(in_w04, 16)
        partial_sum += to_signed(in_x05, 16) * to_signed(in_w05, 16)
        partial_sum += to_signed(in_x06, 16) * to_signed(in_w06, 16)

        partial_sum += to_signed(in_x10, 16) * to_signed(in_w10, 16)
        partial_sum += to_signed(in_x11, 16) * to_signed(in_w11, 16)
        partial_sum += to_signed(in_x12, 16) * to_signed(in_w12, 16)
        partial_sum += to_signed(in_x13, 16) * to_signed(in_w13, 16)
        partial_sum += to_signed(in_x14, 16) * to_signed(in_w14, 16)
        partial_sum += to_signed(in_x15, 16) * to_signed(in_w15, 16)
        partial_sum += to_signed(in_x16, 16) * to_signed(in_w16, 16)

        partial_sum += to_signed(in_x20, 16) * to_signed(in_w20, 16)
        partial_sum += to_signed(in_x21, 16) * to_signed(in_w21, 16)
        partial_sum += to_signed(in_x22, 16) * to_signed(in_w22, 16)
        partial_sum += to_signed(in_x23, 16) * to_signed(in_w23, 16)
        partial_sum += to_signed(in_x24, 16) * to_signed(in_w24, 16)
        partial_sum += to_signed(in_x25, 16) * to_signed(in_w25, 16)
        partial_sum += to_signed(in_x26, 16) * to_signed(in_w26, 16)

        partial_sum += to_signed(in_x30, 16) * to_signed(in_w30, 16)
        partial_sum += to_signed(in_x31, 16) * to_signed(in_w31, 16)
        partial_sum += to_signed(in_x32, 16) * to_signed(in_w32, 16)
        partial_sum += to_signed(in_x33, 16) * to_signed(in_w33, 16)
        partial_sum += to_signed(in_x34, 16) * to_signed(in_w34, 16)
        partial_sum += to_signed(in_x35, 16) * to_signed(in_w35, 16)
        partial_sum += to_signed(in_x36, 16) * to_signed(in_w36, 16)

        partial_sum += to_signed(in_x40, 16) * to_signed(in_w40, 16)
        partial_sum += to_signed(in_x41, 16) * to_signed(in_w41, 16)
        partial_sum += to_signed(in_x42, 16) * to_signed(in_w42, 16)
        partial_sum += to_signed(in_x43, 16) * to_signed(in_w43, 16)
        partial_sum += to_signed(in_x44, 16) * to_signed(in_w44, 16)
        partial_sum += to_signed(in_x45, 16) * to_signed(in_w45, 16)
        partial_sum += to_signed(in_x46, 16) * to_signed(in_w46, 16)

        partial_sum += to_signed(in_x50, 16) * to_signed(in_w50, 16)
        partial_sum += to_signed(in_x51, 16) * to_signed(in_w51, 16)
        partial_sum += to_signed(in_x52, 16) * to_signed(in_w52, 16)
        partial_sum += to_signed(in_x53, 16) * to_signed(in_w53, 16)
        partial_sum += to_signed(in_x54, 16) * to_signed(in_w54, 16)
        partial_sum += to_signed(in_x55, 16) * to_signed(in_w55, 16)
        partial_sum += to_signed(in_x56, 16) * to_signed(in_w56, 16)

        partial_sum += to_signed(in_x60, 16) * to_signed(in_w60, 16)
        partial_sum += to_signed(in_x61, 16) * to_signed(in_w61, 16)
        partial_sum += to_signed(in_x62, 16) * to_signed(in_w62, 16)
        partial_sum += to_signed(in_x63, 16) * to_signed(in_w63, 16)
        partial_sum += to_signed(in_x64, 16) * to_signed(in_w64, 16)
        partial_sum += to_signed(in_x65, 16) * to_signed(in_w65, 16)
        partial_sum += to_signed(in_x66, 16) * to_signed(in_w66, 16)

        return {"out_y": to_unsigned(partial_sum, 32)}