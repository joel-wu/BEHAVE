class MatrixNormDlange:
    def __init__(self):
        self.reset()

    def reset(self):
        self.a = [0] * 16

    def process(self, in_norm):
        def to_unsigned(v, b):
            return v & ((1 << b) - 1)

        values = []
        abs_values = []
        for addr in range(16):
            raw = self.a[addr] & 0xFFFFFFFF
            sign = (raw >> 31) & 1
            mag = ((~raw) + 1) & 0xFFFFFFFF if sign else raw
            values.append(raw - (sign << 32))
            abs_values.append(mag if sign else raw)

        maxabs = abs_values[0]
        for addr in range(1, 16):
            temp = abs_values[addr]
            maxabs = temp if maxabs < temp else maxabs

        colsum0 = abs_values[0] + abs_values[1] + abs_values[2] + abs_values[3]
        colsum1 = abs_values[4] + abs_values[5] + abs_values[6] + abs_values[7]
        colsum2 = abs_values[8] + abs_values[9] + abs_values[10] + abs_values[11]
        colsum3 = abs_values[12] + abs_values[13] + abs_values[14] + abs_values[15]
        maxcol01 = colsum1 if colsum0 < colsum1 else colsum0
        maxcol23 = colsum3 if colsum2 < colsum3 else colsum2
        maxcol = maxcol23 if maxcol01 < maxcol23 else maxcol01

        rowsum0 = abs_values[0] + abs_values[4] + abs_values[8] + abs_values[12]
        rowsum1 = abs_values[1] + abs_values[5] + abs_values[9] + abs_values[13]
        rowsum2 = abs_values[2] + abs_values[6] + abs_values[10] + abs_values[14]
        rowsum3 = abs_values[3] + abs_values[7] + abs_values[11] + abs_values[15]
        maxrow01 = rowsum1 if rowsum0 < rowsum1 else rowsum0
        maxrow23 = rowsum3 if rowsum2 < rowsum3 else rowsum2
        maxrow = maxrow23 if maxrow01 < maxrow23 else maxrow01

        sumsq = 0
        for addr in range(16):
            x = values[addr]
            sumsq += x * x

        root = 0
        rem = 0
        for i in range(33, -1, -1):
            rem = (rem << 2) | ((sumsq >> (i * 2)) & 0x3)
            cand = (root << 2) | 1
            take = rem >= cand
            rem = rem - cand if take else rem
            root = ((root << 1) | 1) if take else (root << 1)

        results = [maxabs, maxcol, maxrow, root]
        return {"out_value": to_unsigned(results[in_norm], 64)}
