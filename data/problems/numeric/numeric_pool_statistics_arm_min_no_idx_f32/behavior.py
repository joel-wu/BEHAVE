def ordered_gt(a, b):
    a_mag, b_mag = a & 0x7FFFFFFF, b & 0x7FFFFFFF
    if a_mag > 0x7F800000 or b_mag > 0x7F800000:
        return False
    if a_mag == 0 and b_mag == 0:
        return False
    if (a >> 31) != (b >> 31):
        return (a >> 31) == 0
    return a_mag < b_mag if a >> 31 else a_mag > b_mag

def to_unsigned(v, b):
    return v & ((1 << b) - 1)

class VectorMinNoIdxFp32:
    def __init__(self):
        self.src = [0] * 16

    def reset(self):
        # Reset does not clear the externally supplied samples.
        pass

    def process(self, in_base_addr):
        base_addr = to_unsigned(in_base_addr, 4)
        min_value = 0x7F7FFFFF
        for i in range(16):
            new_val = self.src[(base_addr + i) & 0xF]
            if ordered_gt(min_value, new_val):
                min_value = new_val
        return {"out_min_value": to_unsigned(min_value, 32)}
