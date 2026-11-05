class Systolic4x4OS:
    def __init__(self):
        self.reset()

    def reset(self):
        self.a = [0] * 16
        self.b = [0] * 16
        self.d = [0] * 16
        self.valid = [0] * 16
        self.prop = [0] * 16
        self.ids = [0] * 16
        self.last = [0] * 16
        self.c1 = [0] * 16
        self.c2 = [0] * 16

    def process(self, in_a, in_b, in_d, in_lanes, in_propagate, in_last, in_ids):
        # All PE outputs use the old registers; no same-tick neighbor writes.
        c = [0] * 16
        for k in range(16):
            c[k] = self.c1[k] if self.prop[k] else self.c2[k]
        ob = 0
        oc = 0
        ov = 0
        op = 0
        oi = 0
        ol = 0
        for col in range(4):
            k = 12 + col
            if self.valid[k]:
                ob |= self.b[k] << (16 * col)
                oc |= c[k] << (16 * col)
                ov |= 1 << col
                op |= self.prop[k] << col
                oi |= self.ids[k] << (2 * col)
                ol |= self.last[k] << col
        for k in range(16):
            if self.valid[k]:
                product = self.a[k] * self.b[k]
                if self.prop[k]:
                    self.c2[k] = (self.c2[k] + product) & 65535
                    self.c1[k] = self.d[k]
                else:
                    self.c1[k] = (self.c1[k] + product) & 65535
                    self.c2[k] = self.d[k]
        # Descending order implements simultaneous edge registers without aliases.
        for k0 in range(16):
            k = 15 - k0
            row = k // 4
            col = k % 4
            self.a[k] = ((in_a >> (16 * row)) & 65535) if col == 0 else self.a[k - 1]
            valid = ((in_lanes >> col) & 1) if row == 0 else self.valid[k - 4]
            if valid:
                self.b[k] = ((in_b >> (16 * col)) & 65535) if row == 0 else self.b[k - 4]
                self.d[k] = ((in_d >> (16 * col)) & 65535) if row == 0 else c[k - 4]
                self.prop[k] = ((in_propagate >> col) & 1) if row == 0 else self.prop[k - 4]
            self.ids[k] = ((in_ids >> (2 * col)) & 3) if row == 0 else self.ids[k - 4]
            self.last[k] = ((in_last >> col) & 1) if row == 0 else self.last[k - 4]
            self.valid[k] = valid
        return {"out_b": ob, "out_c": oc, "out_lanes": ov,
                "out_propagate": op, "out_ids": oi, "out_last": ol}
