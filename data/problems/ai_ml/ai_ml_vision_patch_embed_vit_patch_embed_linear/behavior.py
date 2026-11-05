def signed_lane(packed, index, width):
    value = (packed >> (index * width)) & ((1 << width) - 1)
    if value >= (1 << (width - 1)):
        value -= 1 << width
    return value


class PatchEmbedLinear:
    def __init__(self, patch_h=2, patch_w=2, in_chans=3, embed_dim=4, data_w=16):
        assert 1 <= patch_h <= 4 and 1 <= patch_w <= 4
        assert 1 <= in_chans <= 4 and 1 <= embed_dim <= 8
        assert 8 <= data_w <= 16
        self.PATCH_H = patch_h
        self.PATCH_W = patch_w
        self.IN_CHANS = in_chans
        self.EMBED_DIM = embed_dim
        self.DATA_W = data_w
        self.reset()

    def reset(self):
        pass

    def process(self, in_patch, in_weight, in_bias, in_use_bias):
        terms = self.PATCH_H * self.PATCH_W * self.IN_CHANS
        output = 0
        for row in range(self.EMBED_DIM):
            total = signed_lane(in_bias, row, 32) if in_use_bias else 0
            for column in range(terms):
                pixel = signed_lane(in_patch, column, self.DATA_W)
                weight = signed_lane(in_weight, row * terms + column, self.DATA_W)
                total += pixel * weight
            output |= (total & 0xFFFFFFFF) << (32 * row)
        return {"out_data": output}
