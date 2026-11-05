def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class signal_processing_isp_bayer_auto_exposure:
    def __init__(self):
        self.reset()

    def reset(self):
        self.idle = 0

    def process(
        self,
        in_top_offset,
        in_bottom_offset,
        in_left_offset,
        in_right_offset,
        in_center_illuminance,
        in_histogram_skewness,
        in_pixels,
    ):
        top = in_top_offset
        bottom = 4 if in_bottom_offset == 0 else 4 - in_bottom_offset
        left = in_left_offset
        right = 4 if in_right_offset == 0 else 4 - in_right_offset

        count = 0
        sum2 = 0
        sum3 = 0
        center = in_center_illuminance

        for row in range(4):
            for col in range(4):
                if row >= top and row < bottom and col >= left and col < right:
                    idx = (row << 2) + col
                    p = (in_pixels >> (idx * 8)) & 0xFF
                    d = p - center
                    sum2 += d * d
                    sum3 += d * d * d
                    count += 1

        nonzero_count = count != 0
        m2 = (sum2 // count) if nonzero_count else 0
        m3_pos = sum3 >= 0
        m3 = (sum3 // count) if (nonzero_count and m3_pos) else (-((-sum3) // count) if nonzero_count else 0)

        sign_m3 = -1 if m3 < 0 else 1
        m2_shift = m2 >> 6
        m3_shift = ((-m3) if m3 < 0 else m3) >> 9

        # Preserve the source's thirteen integer Newton iterations. Its
        # floor/ceil oscillation is observable and is not exact integer sqrt.
        sqrt_m2 = m2_shift // 2
        for _ in range(13):
            if sqrt_m2 != 0:
                sqrt_m2 = (sqrt_m2 + m2_shift // sqrt_m2) // 2

        denominator = m2_shift * sqrt_m2
        q = ((m3_shift * 256) // denominator) if denominator != 0 else 0
        skewness = sign_m3 * q

        threshold = in_histogram_skewness
        if skewness < -threshold:
            feedback = 0b11
        elif skewness > threshold:
            feedback = 0b01
        else:
            feedback = 0b00

        return {
            "out_feedback": to_unsigned(feedback, 2),
            "out_skewness": to_unsigned(skewness, 64),
        }
