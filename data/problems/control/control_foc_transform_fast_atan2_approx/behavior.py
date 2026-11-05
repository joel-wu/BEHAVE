def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


C3 = -99850786
C2 = 342124625
C1 = -703564562
PI_OVER_2 = 3373259426
PI = 6746518852


def q31_mul_tz(p, q):
    v = p * q
    if v >= 0:
        return v >> 31
    return -((-v) >> 31)


class ControlFocTransformFastAtan2Approx:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_y, in_x):
        x = to_signed(in_x, 16)
        y = to_signed(in_y, 16)

        abs_x = -x if x < 0 else x
        abs_y = -y if y < 0 else y

        if abs_x < abs_y:
            min_abs = abs_x
            max_abs = abs_y
        else:
            min_abs = abs_y
            max_abs = abs_x

        if max_abs == 0:
            angle = 0
        else:
            a_q15 = (min_abs << 15) // max_abs
            s_q15 = (a_q15 * a_q15) >> 15

            a_q31 = a_q15 << 16
            s_q31 = s_q15 << 16

            p0 = q31_mul_tz(C3, s_q31)
            p1 = p0 + C2
            p2 = q31_mul_tz(p1, s_q31)
            p3 = p2 + C1
            p4 = q31_mul_tz(p3, s_q31)
            p5 = q31_mul_tz(p4, a_q31)
            angle = p5 + a_q31

            if abs_y > abs_x:
                angle = PI_OVER_2 - angle
            if x < 0:
                angle = PI - angle
            if y < 0:
                angle = -angle

        # The polynomial uses widened Q31; the external angle is Q29 radians.
        angle = (angle >> 2) if angle >= 0 else -((-angle) >> 2)
        return {"out_angle": to_unsigned(angle, 32)}
