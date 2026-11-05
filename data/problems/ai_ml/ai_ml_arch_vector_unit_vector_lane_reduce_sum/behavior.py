def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class VectorLaneReduceSum:
    def __init__(self, in_width=8):
        self.in_width = in_width
        self.out_width = in_width + 4
        self.reset()

    def reset(self):
        self.in_width = self.in_width
        self.out_width = self.out_width

    def process(
        self,
        in_lane_0, in_lane_1, in_lane_2, in_lane_3,
        in_lane_4, in_lane_5, in_lane_6, in_lane_7,
        in_lane_8, in_lane_9, in_lane_10, in_lane_11,
        in_lane_12, in_lane_13, in_lane_14, in_lane_15
    ):
        lane0 = to_unsigned(in_lane_0, self.in_width)
        lane1 = to_unsigned(in_lane_1, self.in_width)
        lane2 = to_unsigned(in_lane_2, self.in_width)
        lane3 = to_unsigned(in_lane_3, self.in_width)
        lane4 = to_unsigned(in_lane_4, self.in_width)
        lane5 = to_unsigned(in_lane_5, self.in_width)
        lane6 = to_unsigned(in_lane_6, self.in_width)
        lane7 = to_unsigned(in_lane_7, self.in_width)
        lane8 = to_unsigned(in_lane_8, self.in_width)
        lane9 = to_unsigned(in_lane_9, self.in_width)
        lane10 = to_unsigned(in_lane_10, self.in_width)
        lane11 = to_unsigned(in_lane_11, self.in_width)
        lane12 = to_unsigned(in_lane_12, self.in_width)
        lane13 = to_unsigned(in_lane_13, self.in_width)
        lane14 = to_unsigned(in_lane_14, self.in_width)
        lane15 = to_unsigned(in_lane_15, self.in_width)

        s0 = lane0 + lane1
        s1 = lane2 + lane3
        s2 = lane4 + lane5
        s3 = lane6 + lane7
        s4 = lane8 + lane9
        s5 = lane10 + lane11
        s6 = lane12 + lane13
        s7 = lane14 + lane15

        t0 = s0 + s1
        t1 = s2 + s3
        t2 = s4 + s5
        t3 = s6 + s7

        u0 = t0 + t1
        u1 = t2 + t3

        out_sum = u0 + u1
        return {"out_sum": to_unsigned(out_sum, self.out_width)}