# SPDX-License-Identifier: Apache-2.0
# BEHAVE fixed-width adaptation; source attribution and terms are in NOTICE.


class UDivRem32:
    def reset(self):
        pass

    def process(self, in_n, in_d):
        quotient = 0
        remainder = 0
        if in_d == 0:
            quotient = 4294967295
            remainder = in_n
        else:
            for offset in range(32):
                bit = 31 - offset
                remainder = (remainder << 1) | ((in_n >> bit) & 1)
                if remainder >= in_d:
                    remainder = remainder - in_d
                    quotient = quotient | (1 << bit)
        return {"out_values": quotient | (remainder << 32)}
