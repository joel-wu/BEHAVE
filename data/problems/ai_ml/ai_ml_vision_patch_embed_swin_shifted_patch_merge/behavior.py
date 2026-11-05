"""Bounded Q8.8 instance of Swin's pad/gather/LayerNorm/reduction stage."""


def trunc_div(value, divisor):
    return value // divisor if value >= 0 else -((-value) // divisor)


def isqrt33(value):
    root = 0
    for bit in range(16, -1, -1):
        candidate = root | (1 << bit)
        if candidate * candidate <= value:
            root = candidate
    return root


def signed16(value):
    value &= 0xffff
    return value - 65536 if value >= 32768 else value


def saturate16(value):
    return max(-32768, min(32767, value))


class AiMlVisionPatchEmbedSwinShiftedPatchMerge:
    def __init__(self, data_w=16, in_h=4, in_w=4, dim=2, out_dim=4):
        assert data_w == 16 and 1 <= in_h <= 8 and 1 <= in_w <= 8
        assert 1 <= dim <= 4 and 1 <= out_dim <= 8
        self.in_h = in_h
        self.in_w = in_w
        self.dim = dim
        self.out_dim = out_dim
        self.reset()

    def reset(self):
        pass

    def process(self, in_x):
        result = 0
        n = 4 * self.dim
        for oh in range((self.in_h + 1) // 2):
            for ow in range((self.in_w + 1) // 2):
                # Swin concatenates TL, BL, TR, BR, each with C channels.
                merged = [0] * n
                for k in range(n):
                    corner = k // self.dim
                    h = 2 * oh + corner % 2
                    w = 2 * ow + corner // 2
                    if h < self.in_h and w < self.in_w:
                        index = (h * self.in_w + w) * self.dim + k % self.dim
                        merged[k] = signed16(in_x >> (16 * index))

                mean = trunc_div(sum(merged), n)
                variance_sum = 0
                for value in merged:
                    variance_sum += (value - mean) * (value - mean)
                variance = variance_sum // n
                # floor(65536 / sqrt(variance + 1)), without host floating point.
                inv_std = isqrt33((1 << 32) // (variance + 1))
                affine = [0] * n
                for k in range(n):
                    norm = trunc_div((merged[k] - mean) * inv_std, 256)
                    gamma = 256 + 32 * (k % 3 - 1)
                    beta = 16 * (k % 5 - 2)
                    affine[k] = saturate16(trunc_div(norm * gamma, 256) + beta)
                for o in range(self.out_dim):
                    acc = 0
                    for k in range(n):
                        weight = 32 * (((o + 1) * (k + 3) + o * k) % 17 - 8)
                        acc += trunc_div(affine[k] * weight, 256)
                    index = ((oh * ((self.in_w + 1) // 2) + ow) * self.out_dim + o)
                    result |= (saturate16(acc) & 0xffff) << (16 * index)
        return {"out_y": result}
