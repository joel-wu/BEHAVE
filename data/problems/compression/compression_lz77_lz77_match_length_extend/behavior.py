class CompressionLz77Lz77MatchLengthExtend:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_str_bytes, in_match_bytes, in_start_len, in_max_len):
        if in_start_len > in_max_len or in_max_len > 64:
            return {"out_len": 0, "out_error": 1}
        for i in range(in_start_len, in_max_len):
            if ((in_str_bytes ^ in_match_bytes) >> (8 * i)) & 255:
                return {"out_len": i, "out_error": 0}
        return {"out_len": in_max_len, "out_error": 0}
