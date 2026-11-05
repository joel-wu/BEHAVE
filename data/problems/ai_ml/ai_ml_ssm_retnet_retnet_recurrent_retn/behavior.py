from fp_lib import fp32_add, fp32_mul


class MatrixRetention:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = [0] * 8

    def process(self, in_q, in_k, in_v, in_has_initial_state,
                in_initial_state, in_scale_valid, in_scale, in_output_final_state):
        scale = in_scale if in_scale_valid else 0x3F3504F3
        output = 0
        packed_state = 0
        for head in range(2):
            decay = 0x3F780000 if head == 0 else 0x3F7C0000
            for column in range(2):
                terms = [0, 0]
                for row in range(2):
                    index = 4 * head + 2 * row + column
                    old = (in_initial_state >> (32 * index)) & 0xFFFFFFFF if in_has_initial_state else self.state[index]
                    key = (in_k >> (32 * (2 * head + row))) & 0xFFFFFFFF
                    value = (in_v >> (32 * (2 * head + column))) & 0xFFFFFFFF
                    updated = fp32_add(fp32_mul(old, decay), fp32_mul(key, value))
                    self.state[index] = updated
                    packed_state = packed_state | (updated << (32 * index))
                    query = (in_q >> (32 * (2 * head + row))) & 0xFFFFFFFF
                    terms[row] = fp32_mul(fp32_mul(query, scale), updated)
                result = fp32_add(terms[0], terms[1])
                output = output | (result << (32 * (2 * head + column)))
        return {"out_o": output, "out_final_state_valid": in_output_final_state,
                "out_final_state": packed_state if in_output_final_state else 0}
