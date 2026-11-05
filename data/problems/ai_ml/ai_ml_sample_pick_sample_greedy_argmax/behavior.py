class GreedyArgmax:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_logits):
        best = in_logits & 0xFFFFFFFF
        index = 0
        has_nan = (best & 0x7FFFFFFF) > 0x7F800000
        for lane in range(1, 8):
            value = (in_logits >> (32 * lane)) & 0xFFFFFFFF
            nan = (value & 0x7FFFFFFF) > 0x7F800000
            take = False
            if not has_nan:
                if nan:
                    take = True
                elif (best & 0x7FFFFFFF) == 0 and (value & 0x7FFFFFFF) == 0:
                    pass
                else:
                    key_best = (best ^ 0xFFFFFFFF) if best & 0x80000000 else (best ^ 0x80000000)
                    key_value = (value ^ 0xFFFFFFFF) if value & 0x80000000 else (value ^ 0x80000000)
                    take = key_value > key_best
            if take:
                best = value
                index = lane
                has_nan = nan
        return {"out_index": index, "out_has_nan": int(has_nan)}
