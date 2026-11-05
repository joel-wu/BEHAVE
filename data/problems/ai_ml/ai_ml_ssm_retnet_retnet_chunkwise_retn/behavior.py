from fp_lib import fp32_add, fp32_mul


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlSsmRetnetRetnetChunkwiseRetn:
    DECAY = 0x3F780000
    FP32_ONE = 0x3F800000

    def __init__(self):
        self.reset()

    def reset(self):
        self.retained_state = 0x00000000

    def process(
        self,
        in_q,
        in_k,
        in_v,
        in_has_initial_state,
        in_initial_state,
        in_scale_valid,
        in_scale,
    ):
        state_prev = in_initial_state if in_has_initial_state else self.retained_state
        scale_used = in_scale if in_scale_valid else self.FP32_ONE
        decayed_state = fp32_mul(state_prev, self.DECAY)
        kv_update = fp32_mul(in_k, in_v)
        state_next = fp32_add(decayed_state, kv_update)
        q_scaled = fp32_mul(in_q, scale_used)
        out_o = fp32_mul(q_scaled, state_next)
        self.retained_state = state_next
        return {
            "out_o": to_unsigned(out_o, 32),
            "out_final_state_valid": 1,
            "out_final_state": to_unsigned(state_next, 32),
        }
