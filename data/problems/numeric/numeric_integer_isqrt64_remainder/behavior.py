# SPDX-License-Identifier: Unlicense
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class ISqrt64Remainder:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_n):
        root=0
        remainder=0
        for i in range(32):
            remainder=(remainder << 2) | ((in_n >> (62-2*i)) & 3)
            trial=(root << 2) | 1
            root <<= 1
            if remainder >= trial:
                remainder -= trial
                root |= 1
        result=root | (remainder << 32)
        return {"out_values": result}
