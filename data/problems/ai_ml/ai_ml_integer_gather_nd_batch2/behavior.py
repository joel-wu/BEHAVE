# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class GatherNDBatch2:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_indices):
        result=0
        for b in range(2):
            for q in range(3):
                x=(in_indices >> (6*(3*b+q))) & 7
                y=(in_indices >> (6*(3*b+q)+3)) & 7
                x=x & 3
                y=y & 3
                for c in range(2):
                    src=((4*b+x)*4+y)*2+c
                    result |= ((in_data >> (8*src)) & 255) << (8*(6*b+2*q+c))
        return {"out_values": result}
