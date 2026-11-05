# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class ReflectPad3x3:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data):
        result = 0
        for h in range(5):
            ih = 1 if h == 0 or h == 4 else h - 1
            for w in range(5):
                iw = 1 if w == 0 or w == 4 else w - 1
                result |= ((in_data >> (8*(3*ih+iw))) & 255) << (8*(5*h+w))
        return {"out_values": result}
