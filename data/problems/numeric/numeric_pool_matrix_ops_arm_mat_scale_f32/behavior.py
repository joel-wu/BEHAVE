from fp_lib import fp32_mul


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class MatrixScaleFp324x4:
    def __init__(self, mem_init=None):
        self.data = [0] * 65536
        if mem_init is not None:
            for address, word in mem_init.get("data", {}).items():
                self.data[address & 0xFFFF] = word & 0xFFFFFFFF
        self.reset()

    def reset(self):
        # Reset computation, not the independently bound external store.
        pass

    def process(self, in_src_rows, in_src_cols, in_dst_rows, in_dst_cols,
                in_src_base_addr, in_dst_base_addr, in_scale):
        if (in_src_rows != 4 or in_src_cols != 4 or
                in_dst_rows != 4 or in_dst_cols != 4):
            return {"out_status": 1}

        src_base_addr = to_unsigned(in_src_base_addr, 16)
        dst_base_addr = to_unsigned(in_dst_base_addr, 16)
        scale = to_unsigned(in_scale, 32)

        for i in range(16):
            src_i = self.data[to_unsigned(src_base_addr + i, 16)]
            result_i = fp32_mul(src_i, scale)
            self.data[to_unsigned(dst_base_addr + i, 16)] = to_unsigned(result_i, 32)

        return {"out_status": 0}
