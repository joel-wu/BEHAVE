def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class FecRep5:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_mode,
        in_data0, in_data1, in_data2, in_data3, in_data4,
        in_data5, in_data6, in_data7, in_data8, in_data9,
        in_data10, in_data11, in_data12, in_data13, in_data14,
        in_data15, in_data16, in_data17, in_data18, in_data19,
        in_data20, in_data21, in_data22, in_data23, in_data24,
        in_data25, in_data26, in_data27, in_data28, in_data29,
        in_data30, in_data31, in_data32, in_data33, in_data34,
        in_data35, in_data36, in_data37, in_data38, in_data39,
    ):
        if in_mode == 1:
            s0 = in_data0
            s1 = in_data1
            s2 = in_data2
            s3 = in_data3
            s4 = in_data4
            out_data = (
                (s0 & s1 & s2) |
                (s0 & s1 & s3) |
                (s0 & s1 & s4) |
                (s0 & s2 & s3) |
                (s0 & s2 & s4) |
                (s0 & s3 & s4) |
                (s1 & s2 & s3) |
                (s1 & s2 & s4) |
                (s1 & s3 & s4) |
                (s2 & s3 & s4)
            )
            return {"out_data": to_unsigned(out_data, 8)}

        if in_mode == 2:
            out_data = 0
            sum0 = in_data0 + in_data8 + in_data16 + in_data24 + in_data32
            if sum0 >= 640:
                out_data |= 1 << 7
            sum1 = in_data1 + in_data9 + in_data17 + in_data25 + in_data33
            if sum1 >= 640:
                out_data |= 1 << 6
            sum2 = in_data2 + in_data10 + in_data18 + in_data26 + in_data34
            if sum2 >= 640:
                out_data |= 1 << 5
            sum3 = in_data3 + in_data11 + in_data19 + in_data27 + in_data35
            if sum3 >= 640:
                out_data |= 1 << 4
            sum4 = in_data4 + in_data12 + in_data20 + in_data28 + in_data36
            if sum4 >= 640:
                out_data |= 1 << 3
            sum5 = in_data5 + in_data13 + in_data21 + in_data29 + in_data37
            if sum5 >= 640:
                out_data |= 1 << 2
            sum6 = in_data6 + in_data14 + in_data22 + in_data30 + in_data38
            if sum6 >= 640:
                out_data |= 1 << 1
            sum7 = in_data7 + in_data15 + in_data23 + in_data31 + in_data39
            if sum7 >= 640:
                out_data |= 1 << 0
            return {"out_data": to_unsigned(out_data, 8)}

        out = {"out_data": to_unsigned(in_data0, 8)}
        return [out, out, out, out, out]