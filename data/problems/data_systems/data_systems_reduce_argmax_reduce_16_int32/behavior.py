def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class ReduceArgMaxReduce16Int32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_data_0,
        in_data_1,
        in_data_2,
        in_data_3,
        in_data_4,
        in_data_5,
        in_data_6,
        in_data_7,
        in_data_8,
        in_data_9,
        in_data_10,
        in_data_11,
        in_data_12,
        in_data_13,
        in_data_14,
        in_data_15,
    ):
        x0 = to_signed(in_data_0, 32)
        x1 = to_signed(in_data_1, 32)
        x2 = to_signed(in_data_2, 32)
        x3 = to_signed(in_data_3, 32)
        x4 = to_signed(in_data_4, 32)
        x5 = to_signed(in_data_5, 32)
        x6 = to_signed(in_data_6, 32)
        x7 = to_signed(in_data_7, 32)
        x8 = to_signed(in_data_8, 32)
        x9 = to_signed(in_data_9, 32)
        x10 = to_signed(in_data_10, 32)
        x11 = to_signed(in_data_11, 32)
        x12 = to_signed(in_data_12, 32)
        x13 = to_signed(in_data_13, 32)
        x14 = to_signed(in_data_14, 32)
        x15 = to_signed(in_data_15, 32)

        best_value = x0
        best_index = 0

        if x1 > best_value:
            best_value = x1
            best_index = 1
        if x2 > best_value:
            best_value = x2
            best_index = 2
        if x3 > best_value:
            best_value = x3
            best_index = 3
        if x4 > best_value:
            best_value = x4
            best_index = 4
        if x5 > best_value:
            best_value = x5
            best_index = 5
        if x6 > best_value:
            best_value = x6
            best_index = 6
        if x7 > best_value:
            best_value = x7
            best_index = 7
        if x8 > best_value:
            best_value = x8
            best_index = 8
        if x9 > best_value:
            best_value = x9
            best_index = 9
        if x10 > best_value:
            best_value = x10
            best_index = 10
        if x11 > best_value:
            best_value = x11
            best_index = 11
        if x12 > best_value:
            best_value = x12
            best_index = 12
        if x13 > best_value:
            best_value = x13
            best_index = 13
        if x14 > best_value:
            best_value = x14
            best_index = 14
        if x15 > best_value:
            best_value = x15
            best_index = 15

        return {
            "out_value": to_unsigned(best_value, 32),
            "out_index": to_unsigned(best_index, 4),
        }