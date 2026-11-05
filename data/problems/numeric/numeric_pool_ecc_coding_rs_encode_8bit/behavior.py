def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class RsM1SingleLossRecover64:
    def __init__(self):
        self.reset()

    def reset(self):
        self.buffered = 0

    def process(
        self,
        in_original_count,
        in_present_mask,
        in_recovery_data,
        in_original_data0,
        in_original_data1,
        in_original_data2,
        in_original_data3,
        in_original_data4,
        in_original_data5,
        in_original_data6,
        in_original_data7,
    ):
        original = [
            in_original_data0,
            in_original_data1,
            in_original_data2,
            in_original_data3,
            in_original_data4,
            in_original_data5,
            in_original_data6,
            in_original_data7,
        ]

        count = in_original_count
        if count == 0 or count > 8:
            return {
                "out_missing_index": 0,
                "out_recovered_data": 0,
                "out_status": 1,
            }

        missing_count = 0
        missing_index = 0
        for i in range(8):
            if i < count and (((in_present_mask >> i) & 1) == 0):
                missing_count += 1
                missing_index = i

        if missing_count == 0:
            return {
                "out_missing_index": 0,
                "out_recovered_data": 0,
                "out_status": 2,
            }

        if missing_count > 1:
            return {
                "out_missing_index": 0,
                "out_recovered_data": 0,
                "out_status": 3,
            }

        recovered_data = in_recovery_data
        for i in range(8):
            if i < count and (((in_present_mask >> i) & 1) == 1):
                recovered_data ^= original[i]

        return {
            "out_missing_index": to_unsigned(missing_index, 3),
            "out_recovered_data": to_unsigned(recovered_data, 512),
            "out_status": 0,
        }
