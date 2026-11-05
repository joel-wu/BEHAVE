# SPDX-License-Identifier: Apache-2.0
# BEHAVE fixed-width adaptation; source attribution and terms are in NOTICE.


class Hadamard8:
    def reset(self):
        pass

    def process(self, in_samples):
        result = 0
        for row in range(8):
            value = 0
            for column in range(8):
                sample = (in_samples >> (16 * column)) & 65535
                if sample >= 32768:
                    sample = sample - 65536
                shared = row & column
                negative = (shared & 1) ^ ((shared >> 1) & 1) ^ ((shared >> 2) & 1)
                if negative:
                    value = value - sample
                else:
                    value = value + sample
            result = result | ((value & 524287) << (19 * row))
        return {"out_values": result}
