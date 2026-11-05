class DirectMapCache:
    def __init__(self, CACHE_SIZE=256, DATA_WIDTH=16, TAG_WIDTH=5, OFFSET_WIDTH=3):
        self.CACHE_SIZE = CACHE_SIZE
        self.DATA_WIDTH = DATA_WIDTH
        self.TAG_WIDTH = TAG_WIDTH
        self.OFFSET_WIDTH = OFFSET_WIDTH
        self.reset()

    def reset(self):
        self.tags = [0] * self.CACHE_SIZE
        self.valids = [0] * self.CACHE_SIZE
        self.dirty_bits = [0] * self.CACHE_SIZE
        self.data = [0] * (self.CACHE_SIZE * (1 << (self.OFFSET_WIDTH-1)))

    def process(self, enable, index, offset, comp, write, tag_in, data_in, valid_in):
        hit = 0
        dirty = 0
        tag_out = 0
        data_out = 0
        valid = 0
        error = 0
        if enable:
            if (offset & 1) or index >= self.CACHE_SIZE:
                error = 1
            else:
                cell = index * (1 << (self.OFFSET_WIDTH-1)) + (offset >> 1)
                tag_out = self.tags[index]
                valid = self.valids[index]
                dirty = self.dirty_bits[index]
                data_out = self.data[cell]
                hit = int(bool(comp and valid and tag_out == tag_in))
                if write and (not comp or hit):
                    self.data[cell] = data_in
                    if comp:
                        self.dirty_bits[index] = 1
                    else:
                        self.tags[index] = tag_in
                        self.valids[index] = valid_in
                        self.dirty_bits[index] = 0
        return {"hit": hit, "dirty": dirty, "tag_out": tag_out,
                "data_out": data_out, "valid": valid, "error": error}
