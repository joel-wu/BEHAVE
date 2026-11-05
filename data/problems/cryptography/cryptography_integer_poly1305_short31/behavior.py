# SPDX-License-Identifier: BSD-2-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class Poly1305Short31:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_key, in_message, in_length):
        r = in_key & 0x0ffffffc0ffffffc0ffffffc0fffffff
        s = in_key >> 128
        h = 0
        for block in range(2):
            if in_length > 16*block:
                size = min(16, in_length-16*block)
                word = (in_message >> (128*block)) & ((1 << (8*size))-1)
                h = ((h + word + (1 << (8*size))) * r) % 1361129467683753853853498429727072845819
        result = (h+s) & 340282366920938463463374607431768211455
        return {"out_values": result}
