class Sparse24Pattern:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_weights):
        mask = 0
        dense = 0
        values = 0
        indices = 0
        for group in range(8):
            words = [(in_weights >> (16 * (4 * group + j))) & 65535 for j in range(4)]
            signed = [w - 65536 if w >= 32768 else w for w in words]
            magnitude = [-v if v < 0 else v for v in signed]
            slot = 0
            for j in range(4):
                rank = 0
                for k in range(4):
                    if magnitude[k] > magnitude[j] or (magnitude[k] == magnitude[j] and k < j):
                        rank = rank + 1
                if rank < 2:
                    mask = mask | (1 << (4 * group + j))
                    dense = dense | (words[j] << (16 * (4 * group + j)))
                    values = values | (words[j] << (16 * (2 * group + slot)))
                    indices = indices | (j << (2 * (2 * group + slot)))
                    slot = slot + 1
        return {"out_mask": mask, "out_dense": dense, "out_values": values, "out_indices": indices}
