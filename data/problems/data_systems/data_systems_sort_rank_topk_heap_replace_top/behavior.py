class HeapReplaceTop:
    def __init__(self, K=8, VALUE_W=32, ID_W=64):
        self.K = K
        self.VALUE_W = VALUE_W
        self.ID_W = ID_W
        self.reset()

    def reset(self):
        pass

    def process(self, in_bh_val, in_bh_ids, in_val, in_id):
        value_mask = (1 << self.VALUE_W) - 1
        id_mask = (1 << self.ID_W) - 1

        bh_val = [((in_bh_val >> (i * self.VALUE_W)) & value_mask) for i in range(self.K)]
        bh_ids = [((in_bh_ids >> (i * self.ID_W)) & id_mask) for i in range(self.K)]

        val = in_val & value_mask
        idv = in_id & id_mask
        i = 0

        while True:
            i1 = (i << 1) + 1
            i2 = i1 + 1
            if i1 >= self.K:
                break

            if i2 >= self.K or (bh_val[i1] > bh_val[i2] or (bh_val[i1] == bh_val[i2] and bh_ids[i1] > bh_ids[i2])):
                c = i1
            else:
                c = i2

            if val > bh_val[c] or (val == bh_val[c] and idv > bh_ids[c]):
                break

            bh_val[i] = bh_val[c]
            bh_ids[i] = bh_ids[c]
            i = c

        bh_val[i] = val
        bh_ids[i] = idv

        out_bh_val = 0
        out_bh_ids = 0
        for i in range(self.K):
            out_bh_val |= (bh_val[i] & value_mask) << (i * self.VALUE_W)
            out_bh_ids |= (bh_ids[i] & id_mask) << (i * self.ID_W)

        return {"out_bh_val": out_bh_val, "out_bh_ids": out_bh_ids}