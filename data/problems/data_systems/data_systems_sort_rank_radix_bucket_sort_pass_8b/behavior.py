def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class DataSystemsSortRankRadixBucketSortPass8b:
    def __init__(self):
        self.reset()

    def reset(self):
        self.buckets = [0] * 256
        self.payloads = [0] * 256
        self.frame_length = 0

    def process(self, in_last, in_bucket, in_payload):
        idx = self.frame_length
        self.buckets[idx] = to_unsigned(in_bucket, 8)
        self.payloads[idx] = to_unsigned(in_payload, 32)
        frame_length = idx + 1
        self.frame_length = frame_length
        if not to_unsigned(in_last, 1):
            return None
        out = []
        for bucket_value in range(256):
            for pos in range(frame_length):
                if self.buckets[pos] == bucket_value:
                    out.append({
                        "out_bucket": to_unsigned(self.buckets[pos], 8),
                        "out_payload": to_unsigned(self.payloads[pos], 32),
                        "out_last": 0,
                    })
        out[-1]["out_last"] = 1
        self.buckets = [0] * 256
        self.payloads = [0] * 256
        self.frame_length = 0
        return out
