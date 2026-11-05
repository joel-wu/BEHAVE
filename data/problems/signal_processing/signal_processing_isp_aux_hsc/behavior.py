SIN_Q8 = (
    0, 4, 9, 13, 18, 22, 27, 31, 36, 40, 44, 49, 53,
    58, 62, 66, 71, 75, 79, 83, 88, 92, 96, 100, 104, 108,
    112, 116, 120, 124, 128, 132, 136, 139, 143, 147, 150, 154, 158,
    161, 165, 168, 171, 175, 178, 181, 184, 187, 190, 193, 196, 199,
    202, 204, 207, 210, 212, 215, 217, 219, 222, 224, 226, 228, 230,
    232, 234, 236, 237, 239, 241, 242, 243, 245, 246, 247, 248, 249,
    250, 251, 252, 253, 254, 254, 255, 255, 255, 256, 256, 256, 256,
)


def sin_q8(angle):
    if angle <= 90:
        return SIN_Q8[angle]
    if angle <= 180:
        return SIN_Q8[180 - angle]
    if angle <= 270:
        return -SIN_Q8[angle - 180]
    return -SIN_Q8[360 - angle]


class SignalProcessingIspAuxHsc:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_c0, in_c1, in_hue, in_saturation, in_clip):
        angle = in_hue if in_hue < 360 else in_hue - 360
        cos_angle = angle + 90
        if cos_angle >= 360:
            cos_angle -= 360
        sine, cosine = sin_q8(angle), sin_q8(cos_angle)
        u, v = in_c0 - 128, in_c1 - 128
        rotated_u = u * cosine + v * sine
        rotated_v = v * cosine - u * sine
        value_u = 128 + (in_saturation * rotated_u // 65536)
        value_v = 128 + (in_saturation * rotated_v // 65536)
        return {
            "out_c0": min(in_clip, max(0, value_u)),
            "out_c1": min(in_clip, max(0, value_v)),
        }
