NEG_EXP2_NODES = (1073741824, 1050733751, 1028218693, 1006186087, 984625594, 963527098, 942880699, 922676710, 902905651, 883558244, 864625413, 846098274, 827968132, 810226483, 792865000, 775875538, 759250125, 742980960, 727060411, 711481005, 696235434, 681316545, 666717336, 652430958, 638450708, 624770026, 611382493, 598281827, 585461881, 572916640, 560640218, 548626854, 536870912)


def signed(value, width):
    value = value & ((1 << width) - 1)
    return value - (1 << width) if value & (1 << (width - 1)) else value


def trunc_div(value, divisor):
    return -((-value) // divisor) if value < 0 else value // divisor


def exp_weight(distance):
    # Nonnegative natural-log distance in Q16; Q30 output.
    if distance >= 1048576:
        return 0
    z = distance * 94548 // 65536
    n = z >> 16
    fraction = z & 65535
    j = fraction >> 11
    r = fraction & 2047
    a = NEG_EXP2_NODES[j]
    b = NEG_EXP2_NODES[j + 1]
    return (a - ((a - b) * r // 2048)) >> n


def sqrt_scale(dimension):
    radicand = dimension << 32
    root = 0
    remainder = 0
    for i in range(31, -1, -1):
        remainder = (remainder << 2) | ((radicand >> (2 * i)) & 3)
        trial = (root << 2) | 1
        if remainder >= trial:
            remainder = remainder - trial
            root = (root << 1) | 1
        else:
            root = root << 1
    return root


class ai_ml_tx_attn_sdpa_mistral_sdpa_swa_attn:
    def __init__(self, HEAD_DIM=4, NUM_ATTENTION_HEADS=4, NUM_KEY_VALUE_HEADS=2, SLIDING_WINDOW=8, ELEM_W=32):
        self.dim = HEAD_DIM
        self.heads = NUM_ATTENTION_HEADS
        self.kv_heads = NUM_KEY_VALUE_HEADS
        self.capacity = SLIDING_WINDOW
        self.bits = 32
        self.scale_den = sqrt_scale(HEAD_DIM)
        self.reset()

    def reset(self):
        self.cache_len = 0
        self.key_cache = [[0] * (self.kv_heads * self.dim) for _ in range(self.capacity)]
        self.value_cache = [[0] * (self.kv_heads * self.dim) for _ in range(self.capacity)]

    def process(self, in_query, in_key, in_value):
        keys = [[self.key_cache[t][d] for d in range(self.kv_heads * self.dim)] for t in range(self.capacity)]
        values = [[self.value_cache[t][d] for d in range(self.kv_heads * self.dim)] for t in range(self.capacity)]
        if self.cache_len < self.capacity:
            pos = self.cache_len
        else:
            pos = self.capacity - 1
            for t in range(self.capacity - 1):
                for d in range(self.kv_heads * self.dim):
                    keys[t][d] = self.key_cache[t + 1][d]
                    values[t][d] = self.value_cache[t + 1][d]
        length = pos + 1
        for d in range(self.kv_heads * self.dim):
            keys[pos][d] = signed(in_key >> (self.bits * d), self.bits)
            values[pos][d] = signed(in_value >> (self.bits * d), self.bits)
        result = 0
        for h in range(self.heads):
            kh = h // (self.heads // self.kv_heads)
            scores = [0] * self.capacity
            for t in range(self.capacity):
                if t < length:
                    dot = 0
                    for d in range(self.dim):
                        query = signed(in_query >> (self.bits * (h * self.dim + d)), self.bits)
                        dot = dot + query * keys[t][kh * self.dim + d]
                    scores[t] = trunc_div(dot, self.scale_den)
            maximum = scores[0]
            for t in range(1, self.capacity):
                if t < length:
                    maximum = max(maximum, scores[t])
            weights = [0] * self.capacity
            total = 0
            for t in range(self.capacity):
                if t < length:
                    weights[t] = exp_weight(maximum - scores[t])
                    total = total + weights[t]
            for d in range(self.dim):
                value_sum = 0
                for t in range(self.capacity):
                    if t < length:
                        value_sum = value_sum + weights[t] * values[t][kh * self.dim + d]
                value = trunc_div(value_sum, total)
                result = result | ((value & ((1 << self.bits) - 1)) << (self.bits * (h * self.dim + d)))
        self.cache_len = length
        self.key_cache = keys
        self.value_cache = values
        return {'out_attn': result}
