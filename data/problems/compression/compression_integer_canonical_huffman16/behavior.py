# SPDX-License-Identifier: Zlib
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class CanonicalHuffman16:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_lengths):
        counts=[0]*9
        invalid=0
        for i in range(16):
            length=(in_lengths >> (4*i)) & 15
            if length > 8:
                invalid=1
            elif length:
                counts[length] += 1
        left=1
        code=0
        next_codes=[0]*9
        for length in range(1,9):
            left=2*left-counts[length]
            if left < 0:
                invalid=1
            code=(code+counts[length-1])*2
            next_codes[length]=code
        result=0
        if invalid:
            result=1 << 128
        else:
            for i in range(16):
                length=(in_lengths >> (4*i)) & 15
                if length:
                    code=next_codes[length]
                    next_codes[length] += 1
                    reversed_code=0
                    for j in range(8):
                        if j < length:
                            reversed_code=(reversed_code << 1) | ((code >> j) & 1)
                    result |= reversed_code << (8*i)
        return {"out_values": result}
