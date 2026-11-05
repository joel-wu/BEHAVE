def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class SignalProcessingIspAuxBcc:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_img, in_brightness, in_contrast, in_clip):
        img = to_unsigned(in_img, 8)
        brightness = to_signed(in_brightness, 8)
        contrast = to_signed(in_contrast, 8)
        clip = to_unsigned(in_clip, 8)
        temp = img + brightness
        delta = img - 127
        scaled = delta * contrast
        bcc = temp + scaled
        if bcc < 0:
            out_img = 0
        elif bcc > clip:
            out_img = clip
        else:
            out_img = bcc
        return {"out_img": to_unsigned(out_img, 8)}