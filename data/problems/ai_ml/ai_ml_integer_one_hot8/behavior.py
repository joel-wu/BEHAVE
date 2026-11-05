# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class OneHot8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_labels, in_off, in_on):
        result = 0
        for i in range(8):
            for c in range(8):
                value = in_on if ((in_labels >> (3*i)) & 7) == c else in_off
                result |= value << (8*(8*i+c))
        return {"out_values": result}
