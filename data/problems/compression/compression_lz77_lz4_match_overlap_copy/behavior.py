class CompressionLz77Lz4MatchOverlapCopy:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_offset, in_match_length, in_history):
        if in_match_length == 0:
            return []
        if in_offset < 1 or in_offset > 32:
            return [{"out_byte": 0, "out_error": 1}]
        history = [(in_history >> (8 * i)) & 255 for i in range(32)]
        result = []
        for _ in range(in_match_length):
            byte = history[-in_offset]
            result.append({"out_byte": byte, "out_error": 0})
            history.append(byte)
        return result
