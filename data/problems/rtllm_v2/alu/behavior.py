class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, aluc):
        sa = a - 4294967296 if a & 2147483648 else a
        sb = b - 4294967296 if b & 2147483648 else b
        result = 0
        carry = 0
        overflow = 0
        flag = 0
        if aluc == 32 or aluc == 33:
            result = (a + b) & 4294967295
            carry = (a + b) >> 32
            if aluc == 32:
                overflow = ((a ^ result) & (b ^ result)) >> 31
        elif aluc == 34 or aluc == 35:
            result = (a - b) & 4294967295
            carry = int(a >= b)
            if aluc == 34:
                overflow = ((a ^ b) & (a ^ result)) >> 31
        elif aluc == 36:
            result = a & b
        elif aluc == 37:
            result = a | b
        elif aluc == 38:
            result = a ^ b
        elif aluc == 39:
            result = (a | b) ^ 4294967295
        elif aluc == 42 or aluc == 43:
            flag = int(sa < sb) if aluc == 42 else int(a < b)
            result = flag
        elif aluc == 0 or aluc == 2 or aluc == 3 or aluc == 4 or aluc == 6 or aluc == 7:
            shift = a if aluc == 0 or aluc == 2 or aluc == 3 else a & 31
            if aluc == 0 or aluc == 4:
                result = (b << shift) & 4294967295 if shift < 32 else 0
            elif aluc == 2 or aluc == 6:
                result = b >> shift if shift < 32 else 0
            else:
                result = (sb >> shift) & 4294967295 if shift < 32 else (4294967295 if sb < 0 else 0)
        elif aluc == 15:
            result = (a & 65535) << 16
        return {"r": result, "zero": int(result == 0), "carry": carry,
                "negative": result >> 31, "overflow": overflow, "flag": flag}
