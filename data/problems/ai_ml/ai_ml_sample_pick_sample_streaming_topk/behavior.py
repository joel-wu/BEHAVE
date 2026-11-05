def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlSamplePickSampleStreamingTopk:
    def __init__(self):
        self.reset()

    def reset(self):
        self.frame_active = 0
        self.best_token_id = 0
        self.best_score = 0

    def process(self, in_start, in_last, in_token_id, in_score):
        in_start = to_unsigned(in_start, 1)
        in_last = to_unsigned(in_last, 1)
        in_token_id = to_unsigned(in_token_id, 16)
        in_score = to_unsigned(in_score, 32)
        if in_score >= 2147483648:
            in_score -= 4294967296

        frame_active = self.frame_active
        best_token_id = self.best_token_id
        best_score = self.best_score

        if in_start:
            best_token_id = in_token_id
            best_score = in_score
            frame_active = 1
        elif frame_active:
            if in_score > best_score or (in_score == best_score and in_token_id < best_token_id):
                best_token_id = in_token_id
                best_score = in_score

        result = None
        if in_last:
            result = {"out_token_id": to_unsigned(best_token_id if frame_active else 0, 16)}
            frame_active = 0
            best_token_id = 0
            best_score = 0

        self.frame_active = frame_active
        self.best_token_id = best_token_id
        self.best_score = best_score
        return result
