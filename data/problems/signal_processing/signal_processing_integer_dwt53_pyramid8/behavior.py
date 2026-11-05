# SPDX-License-Identifier: BSD-2-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

class DWT53Pyramid8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data):
        data = [0] * 8
        detail = [0] * 4
        for i in range(8):
            data[i] = s16(in_data >> (16 * i))
        for level in range(3):
            half = 4 >> level
            for i in range(4):
                if i < half:
                    right = i + 1 if i + 1 < half else i
                    detail[i] = data[2*i+1] - ((data[2*i] + data[2*right]) >> 1)
            for i in range(4):
                if i < half:
                    left = i - 1 if i > 0 else 0
                    data[i] = data[2*i] + ((detail[left] + detail[i] + 2) >> 2)
            for i in range(4):
                if i < half:
                    data[half+i] = detail[i]
        result = 0
        for i in range(8):
            result |= (data[i] & 4294967295) << (32*i)
        return {"out_values": result}
