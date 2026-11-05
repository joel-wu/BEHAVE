from behave_intrinsics import uwrap


MASK64 = (1 << 64) - 1


def rotr64(x, n):
    x &= MASK64
    return ((x >> n) | ((x << (64 - n)) & MASK64)) & MASK64


def g(a, b, c, d):
    a = (a + b + 2 * ((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF))) & MASK64
    d = rotr64(d ^ a, 32)
    c = (c + d + 2 * ((c & 0xFFFFFFFF) * (d & 0xFFFFFFFF))) & MASK64
    b = rotr64(b ^ c, 24)
    a = (a + b + 2 * ((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF))) & MASK64
    d = rotr64(d ^ a, 16)
    c = (c + d + 2 * ((c & 0xFFFFFFFF) * (d & 0xFFFFFFFF))) & MASK64
    b = rotr64(b ^ c, 63)
    return a, b, c, d


def blake2_round_16(v):
    v = list(v)
    v[0], v[4], v[8], v[12] = g(v[0], v[4], v[8], v[12])
    v[1], v[5], v[9], v[13] = g(v[1], v[5], v[9], v[13])
    v[2], v[6], v[10], v[14] = g(v[2], v[6], v[10], v[14])
    v[3], v[7], v[11], v[15] = g(v[3], v[7], v[11], v[15])
    v[0], v[5], v[10], v[15] = g(v[0], v[5], v[10], v[15])
    v[1], v[6], v[11], v[12] = g(v[1], v[6], v[11], v[12])
    v[2], v[7], v[8], v[13] = g(v[2], v[7], v[8], v[13])
    v[3], v[4], v[9], v[14] = g(v[3], v[4], v[9], v[14])
    return v


class CryptographySymMemhardArgon2GBlockMix:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_with_xor, in_prev_block, in_ref_block, in_next_block):
        prev = [(in_prev_block >> (64 * j)) & MASK64 for j in range(128)]
        ref = [(in_ref_block >> (64 * j)) & MASK64 for j in range(128)]
        next_in = [(in_next_block >> (64 * j)) & MASK64 for j in range(128)]

        blockR = [(ref[j] ^ prev[j]) & MASK64 for j in range(128)]
        if in_with_xor & 1:
            block_tmp = [(blockR[j] ^ next_in[j]) & MASK64 for j in range(128)]
        else:
            block_tmp = list(blockR)

        for i in range(8):
            base = 16 * i
            group = blockR[base:base + 16]
            group = blake2_round_16(group)
            blockR[base:base + 16] = group

        for i in range(8):
            idx = [2 * i, 2 * i + 1, 2 * i + 16, 2 * i + 17, 2 * i + 32, 2 * i + 33,
                   2 * i + 48, 2 * i + 49, 2 * i + 64, 2 * i + 65, 2 * i + 80, 2 * i + 81,
                   2 * i + 96, 2 * i + 97, 2 * i + 112, 2 * i + 113]
            group = [blockR[k] for k in idx]
            group = blake2_round_16(group)
            for p in range(16):
                blockR[idx[p]] = group[p]

        out_next_block = 0
        for j in range(128):
            out_next_block |= ((block_tmp[j] ^ blockR[j]) & MASK64) << (64 * j)

        return {"out_next_block": uwrap(out_next_block, 8192)}
