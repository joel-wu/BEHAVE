class AiMlQuantKvPagedQuantKvBlock:
    def __init__(self, num_blocks=4, num_heads=2, head_size=8, block_size=4,
                 x=4, elem_w=16, slot_w=32):
        assert (num_blocks > 0 and num_heads > 0 and head_size > 0 and block_size > 0
                and x > 0 and head_size % x == 0 and 1 <= elem_w <= 32 and 2 <= slot_w <= 32
                and num_blocks * block_size < (1 << (slot_w-1))
                and num_blocks * num_heads * head_size * block_size <= 4096), "invalid cache shape"
        self.num_blocks = num_blocks
        self.num_heads = num_heads
        self.head_size = head_size
        self.block_size = block_size
        self.x = x
        self.elem_w = elem_w
        self.slot_w = slot_w
        self.words = num_blocks * num_heads * head_size * block_size
        self.reset()

    def reset(self):
        self.key_cache = [0] * self.words
        self.value_cache = [0] * self.words

    def process(self, in_op, in_slot, in_key, in_value, in_address):
        ok = 0
        wrote = 0
        data = 0
        if in_op == 0:
            slot = in_slot & ((1 << self.slot_w) - 1)
            if slot & (1 << (self.slot_w - 1)):
                slot = slot - (1 << self.slot_w)
            if 0 <= slot < self.num_blocks * self.block_size:
                block = slot // self.block_size
                offset = slot % self.block_size
                for head in range(self.num_heads):
                    for element in range(self.head_size):
                        source = head * self.head_size + element
                        ka = ((((block * self.num_heads + head) * (self.head_size // self.x)
                               + element // self.x) * self.block_size + offset) * self.x + element % self.x)
                        va = ((block * self.num_heads + head) * self.head_size + element) * self.block_size + offset
                        self.key_cache[ka] = (in_key >> (self.elem_w * source)) & ((1 << self.elem_w) - 1)
                        self.value_cache[va] = (in_value >> (self.elem_w * source)) & ((1 << self.elem_w) - 1)
                ok = 1
                wrote = 1
        elif in_op == 1 or in_op == 2:
            if in_address < self.words:
                data = self.key_cache[in_address] if in_op == 1 else self.value_cache[in_address]
                ok = 1
        return {"out_ok": ok, "out_wrote": wrote, "out_data": data}
