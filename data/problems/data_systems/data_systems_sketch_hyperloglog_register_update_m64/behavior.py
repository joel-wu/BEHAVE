def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class HyperLogLogRegisterUpdateM64:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_hash, in_registers):
        in_hash = to_unsigned(in_hash, 32)
        in_registers = to_unsigned(in_registers, 384)

        j = (in_hash >> 26) & 0x3F
        w = in_hash & 0x03FFFFFF

        r = 27
        for i in range(25, -1, -1):
            if ((w >> i) & 1) != 0:
                r = 26 - i
                break

        out_registers = in_registers
        old = (in_registers >> (6 * j)) & 0x3F
        new = old if old >= r else r
        mask = to_unsigned(0x3F << (6 * j), 384)
        out_registers = (out_registers & to_unsigned(~mask, 384)) | (to_unsigned(new, 6) << (6 * j))

        return {"out_registers": to_unsigned(out_registers, 384)}