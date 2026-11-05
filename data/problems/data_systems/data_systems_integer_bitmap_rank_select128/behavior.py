# SPDX-License-Identifier: MIT
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class BitmapRankSelect128:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_bitmap, in_position, in_ordinal):
        rank=0
        count=0
        selected=0
        valid=0
        for i in range(128):
            if (in_bitmap >> i) & 1:
                if i <= in_position:
                    rank += 1
                if count == in_ordinal:
                    selected=i
                    valid=1
                count += 1
        result=rank | (count << 8) | (selected << 16) | (valid << 23)
        return {"out_values": result}
