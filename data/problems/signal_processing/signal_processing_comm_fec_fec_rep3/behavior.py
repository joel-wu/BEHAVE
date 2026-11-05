def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class signal_processing_comm_fec_fec_rep3:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_mode,
        in_data0, in_data1, in_data2, in_data3, in_data4, in_data5,
        in_data6, in_data7, in_data8, in_data9, in_data10, in_data11,
        in_data12, in_data13, in_data14, in_data15, in_data16, in_data17,
        in_data18, in_data19, in_data20, in_data21, in_data22, in_data23,
    ):
        mode = to_unsigned(in_mode, 2)

        if mode == 0 or mode == 3:
            out_data0 = to_unsigned(in_data0, 8)
            out_data1 = to_unsigned(in_data0, 8)
            out_data2 = to_unsigned(in_data0, 8)
        elif mode == 1:
            s0 = to_unsigned(in_data0, 8)
            s1 = to_unsigned(in_data1, 8)
            s2 = to_unsigned(in_data2, 8)
            out_data0 = (s0 & s1) | (s0 & s2) | (s1 & s2)
            out_data1 = 0
            out_data2 = 0
        else:
            sum0 = to_unsigned(in_data0, 8) + to_unsigned(in_data8, 8) + to_unsigned(in_data16, 8)
            sum1 = to_unsigned(in_data1, 8) + to_unsigned(in_data9, 8) + to_unsigned(in_data17, 8)
            sum2 = to_unsigned(in_data2, 8) + to_unsigned(in_data10, 8) + to_unsigned(in_data18, 8)
            sum3 = to_unsigned(in_data3, 8) + to_unsigned(in_data11, 8) + to_unsigned(in_data19, 8)
            sum4 = to_unsigned(in_data4, 8) + to_unsigned(in_data12, 8) + to_unsigned(in_data20, 8)
            sum5 = to_unsigned(in_data5, 8) + to_unsigned(in_data13, 8) + to_unsigned(in_data21, 8)
            sum6 = to_unsigned(in_data6, 8) + to_unsigned(in_data14, 8) + to_unsigned(in_data22, 8)
            sum7 = to_unsigned(in_data7, 8) + to_unsigned(in_data15, 8) + to_unsigned(in_data23, 8)

            out_data0 = 0
            out_data0 |= ((sum0 // 3) > 127) << 7
            out_data0 |= ((sum1 // 3) > 127) << 6
            out_data0 |= ((sum2 // 3) > 127) << 5
            out_data0 |= ((sum3 // 3) > 127) << 4
            out_data0 |= ((sum4 // 3) > 127) << 3
            out_data0 |= ((sum5 // 3) > 127) << 2
            out_data0 |= ((sum6 // 3) > 127) << 1
            out_data0 |= ((sum7 // 3) > 127) << 0
            out_data1 = 0
            out_data2 = 0

        return {
            "out_data0": to_unsigned(out_data0, 8),
            "out_data1": to_unsigned(out_data1, 8),
            "out_data2": to_unsigned(out_data2, 8),
        }