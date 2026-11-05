class AiMlTxKvCacheKvCachePagedRr:
    NUM_BLOCKS = 2
    NUM_KV_HEADS = 2
    HEAD_SIZE = 4
    BLOCK_SIZE = 2
    ELEM_WIDTH = 16
    SLOT_WIDTH = 32

    def __init__(self):
        self.reset()

    def reset(self):
        self.key_cache = [
            [
                [
                    [0 for _ in range(self.BLOCK_SIZE)]
                    for _ in range(self.HEAD_SIZE)
                ]
                for _ in range(self.NUM_KV_HEADS)
            ]
            for _ in range(self.NUM_BLOCKS)
        ]
        self.value_cache = [
            [
                [
                    [0 for _ in range(self.BLOCK_SIZE)]
                    for _ in range(self.HEAD_SIZE)
                ]
                for _ in range(self.NUM_KV_HEADS)
            ]
            for _ in range(self.NUM_BLOCKS)
        ]

    def process(self, in_slot_mapping, in_key, in_value,
                in_read_block, in_read_head, in_read_elem, in_read_slot):
        slot = in_slot_mapping & ((1 << self.SLOT_WIDTH) - 1)
        block_index = slot // self.BLOCK_SIZE
        slot_index = slot - block_index * self.BLOCK_SIZE
        in_range = block_index < self.NUM_BLOCKS

        mask = (1 << self.ELEM_WIDTH) - 1

        # Apply write atomically with this transaction.
        if in_range:
            for h in range(self.NUM_KV_HEADS):
                for e in range(self.HEAD_SIZE):
                    shift = (h * self.HEAD_SIZE + e) * self.ELEM_WIDTH
                    self.key_cache[block_index][h][e][slot_index] = (in_key >> shift) & mask
                    self.value_cache[block_index][h][e][slot_index] = (in_value >> shift) & mask

        # Read the addressed cell after this transaction's write. Every read
        # address fits the fixed port widths and names an existing cell.
        rb = in_read_block & (self.NUM_BLOCKS - 1) if self.NUM_BLOCKS > 1 else 0
        rh = in_read_head & (self.NUM_KV_HEADS - 1) if self.NUM_KV_HEADS > 1 else 0
        re_ = in_read_elem & (self.HEAD_SIZE - 1) if self.HEAD_SIZE > 1 else 0
        rs = in_read_slot & (self.BLOCK_SIZE - 1) if self.BLOCK_SIZE > 1 else 0
        out_key_elem = self.key_cache[rb][rh][re_][rs] & mask
        out_value_elem = self.value_cache[rb][rh][re_][rs] & mask

        return {
            "out_ok": 1 if in_range else 0,
            "out_key_elem": out_key_elem,
            "out_value_elem": out_value_elem,
        }
