class UnpackOneHot:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, sign, size, one_hot_selector, source_reg):
        if one_hot_selector == 1:
            source_width = 1
            dest_width = 8
        elif one_hot_selector == 2:
            source_width = 2
            dest_width = 8
        elif one_hot_selector == 4:
            source_width = 8 if size else 4
            dest_width = 16 if size else 8
        else:
            return {"destination_reg": source_reg}
        result = 0
        for i in range(64):
            if i * dest_width < 512:
                lane = (source_reg >> (i * source_width)) & ((1 << source_width) - 1)
                if sign and (lane & (1 << (source_width - 1))):
                    lane |= ((1 << dest_width) - 1) ^ ((1 << source_width) - 1)
                result |= lane << (i * dest_width)
        return {"destination_reg": result}
