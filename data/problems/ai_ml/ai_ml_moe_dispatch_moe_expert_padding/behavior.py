def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlMoeDispatchMoeExpertPadding:
    def __init__(self, capacity_width=32):
        self.capacity_width = capacity_width
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_tokens,
        in_num_experts,
        in_top_k,
        in_world_size,
        in_capacity_factor_num,
        in_capacity_factor_den,
    ):
        numerator = in_top_k * in_tokens * in_world_size * in_capacity_factor_num
        denominator = in_num_experts * in_capacity_factor_den
        capacity = 0
        if denominator != 0:
            capacity = numerator // denominator
        return {"out_expert_capacity": to_unsigned(capacity, self.capacity_width)}
