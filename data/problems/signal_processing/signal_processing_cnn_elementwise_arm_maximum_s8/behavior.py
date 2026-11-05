"""Signed NHWC maximum with all broadcast addressing inside the task."""


def broadcast_offset(shape, coordinate):
    offset = 0
    for extent, index in zip(shape, coordinate):
        offset = offset * extent + (0 if extent == 1 else index)
    return offset


class SignalProcessingCnnElementwiseArmMaximumS8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_input_1_n, in_input_1_h, in_input_1_w, in_input_1_c, in_input_2_n, in_input_2_h, in_input_2_w, in_input_2_c, in_out_n, in_out_h, in_out_w, in_out_c, in_input_1_data, in_input_2_data):
        shape1 = (in_input_1_n, in_input_1_h, in_input_1_w, in_input_1_c)
        shape2 = (in_input_2_n, in_input_2_h, in_input_2_w, in_input_2_c)
        coordinate = (in_out_n, in_out_h, in_out_w, in_out_c)
        for a, b, index in zip(shape1, shape2, coordinate):
            if not (1 <= a <= 4 and 1 <= b <= 4):
                return {"out_value": 0, "out_error": 1}
            if not (a == b or a == 1 or b == 1) or index >= max(a, b):
                return {"out_value": 0, "out_error": 1}
        offset1 = broadcast_offset(shape1, coordinate)
        offset2 = broadcast_offset(shape2, coordinate)
        a = (in_input_1_data >> (8 * offset1)) & 255
        b = (in_input_2_data >> (8 * offset2)) & 255
        a = a - 256 if a >= 128 else a
        b = b - 256 if b >= 128 else b
        return {"out_value": max(a, b) & 255, "out_error": 0}
