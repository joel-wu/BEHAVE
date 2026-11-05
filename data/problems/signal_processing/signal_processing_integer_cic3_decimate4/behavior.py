# SPDX-License-Identifier: Apache-2.0
# BEHAVE fixed-block CIC adaptation; source attribution and terms are in NOTICE.


class CIC3Decimate4:
    def __init__(self):
        self.reset()

    def reset(self):
        self.i0 = 0
        self.i1 = 0
        self.i2 = 0
        self.c0 = 0
        self.c1 = 0
        self.c2 = 0

    def process(self, in_samples):
        for lane in range(4):
            sample = (in_samples >> (16 * lane)) & 65535
            if sample >= 32768:
                sample = sample - 65536
            self.i0 = (self.i0 + sample) & 4294967295
            self.i1 = (self.i1 + self.i0) & 4294967295
            self.i2 = (self.i2 + self.i1) & 4294967295
        d0 = (self.i2 - self.c0) & 4294967295
        d1 = (d0 - self.c1) & 4294967295
        d2 = (d1 - self.c2) & 4294967295
        self.c0 = self.i2
        self.c1 = d0
        self.c2 = d1
        return {"out_values": d2 & 4194303}
