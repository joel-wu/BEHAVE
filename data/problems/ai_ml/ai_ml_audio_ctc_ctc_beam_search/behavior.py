def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CTCBeamSearch:
    def __init__(self, token_w=8, time_w=8, max_t=16):
        assert token_w >= 1 and time_w >= 1 and 1 <= max_t < (1 << time_w)
        self.token_w = token_w
        self.time_w = time_w
        self.max_t = max_t
        self.reset()

    def reset(self):
        self.pending = 0

    def process(self, in_blank_token, in_length, in_tokens):
        blank = to_unsigned(in_blank_token, self.token_w)
        length = to_unsigned(in_length, self.time_w)
        if length > self.max_t:
            length = self.max_t

        out_tokens = 0
        out_timesteps = 0
        out_len = 0
        prev = 0

        for i in range(self.max_t):
            token = to_unsigned((in_tokens >> (i * self.token_w)), self.token_w)
            if i < length and token != blank and (i == 0 or token != prev):
                out_tokens |= token << (out_len * self.token_w)
                out_timesteps |= to_unsigned(i, self.time_w) << (out_len * self.time_w)
                out_len += 1
            prev = token

        return {
            "out_token_length": to_unsigned(out_len, self.time_w),
            "out_timestep_length": to_unsigned(out_len, self.time_w),
            "out_tokens": to_unsigned(out_tokens, self.max_t * self.token_w),
            "out_timesteps": to_unsigned(out_timesteps, self.max_t * self.time_w),
        }
