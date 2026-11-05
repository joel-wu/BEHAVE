# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class DepthToSpace2:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data):
        result=0
        for h in range(2):
            for w in range(2):
                for i in range(2):
                    for j in range(2):
                        src=4*(2*i+j)+2*h+w
                        dst=4*(2*h+i)+2*w+j
                        result |= ((in_data >> (8*src)) & 255) << (8*dst)
        return {"out_values": result}
