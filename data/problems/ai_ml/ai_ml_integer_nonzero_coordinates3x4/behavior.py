# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class NonZeroCoordinates3x4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data):
        result=0
        count=0
        for i in range(12):
            if ((in_data >> (8*i)) & 255) != 0:
                result |= (i//4) << (2*count)
                result |= (i%4) << (24+2*count)
                count += 1
        result |= count << 48
        return {"out_values": result}
