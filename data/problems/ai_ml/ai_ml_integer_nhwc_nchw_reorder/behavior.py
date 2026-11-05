# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class NHWCNCHWReorder:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_reverse):
        result=0
        for h in range(2):
            for w in range(2):
                for c in range(4):
                    nhwc=(2*h+w)*4+c
                    nchw=4*c+2*h+w
                    src=nchw if in_reverse else nhwc
                    dst=nhwc if in_reverse else nchw
                    result |= ((in_data >> (8*src)) & 255) << (8*dst)
        return {"out_values": result}
