# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class ReverseSequence4x8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_lengths):
        result=0
        for b in range(4):
            n=min(8,(in_lengths >> (4*b)) & 15)
            for t in range(8):
                src=n-1-t if t<n else t
                for c in range(2):
                    result |= ((in_data >> (8*(16*b+2*src+c))) & 255) << (8*(16*b+2*t+c))
        return {"out_values": result}
