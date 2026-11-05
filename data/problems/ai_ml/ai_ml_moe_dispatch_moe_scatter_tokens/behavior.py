from fp_lib import fp32_add, fp32_mul


class MoeScatterTokens:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_experts, in_weights, in_values):
        experts = [(in_experts >> (2 * r)) & 3 for r in range(8)]
        weights = [(in_weights >> (32 * r)) & 0xFFFFFFFF for r in range(8)]
        contribution = [0] * 16
        counts = 0
        indices = 0
        dropped = 0
        position = 0
        for e in range(4):
            count = 0
            for r in range(8):
                if experts[r] == e:
                    indices = indices | (r << (3 * position))
                    position = position + 1
                    if count < 2:
                        for d in range(2):
                            value = (in_values >> (32 * (4 * e + 2 * count + d))) & 0xFFFFFFFF
                            contribution[2 * r + d] = fp32_mul(value, weights[r])
                    else:
                        dropped = dropped | (1 << r)
                    count = count + 1
            counts = counts | (count << (4 * e))
        output = 0
        for t in range(4):
            for d in range(2):
                value = fp32_add(contribution[4 * t + d], contribution[4 * t + 2 + d])
                output = output | (value << (32 * (2 * t + d)))
        return {"out_values": output, "out_dropped": dropped, "out_counts": counts, "out_indices": indices}
