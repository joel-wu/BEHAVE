def signed64(x):
    x = x & 0xFFFFFFFFFFFFFFFF
    return x - (1 << 64) if x >= (1 << 63) else x


def hash_slot(key):
    product = signed64(signed64(key) * 1000003)
    remainder = -(abs(product) % 8955327411143) if product < 0 else product % 8955327411143
    return remainder & 255


class FaissInt64Table:
    def __init__(self):
        self.reset()

    def reset(self):
        self.table = [(1 << 128) - 1] * 256

    def process(self, in_lookup, in_key, in_value):
        if in_key == 0xFFFFFFFFFFFFFFFF:
            return {"out_status": 3, "out_value": 0, "out_slot": 0}
        start = hash_slot(in_key)
        for offset in range(256):
            slot = (start + offset) & 255
            entry = self.table[slot]
            key = entry & 0xFFFFFFFFFFFFFFFF
            if key == in_key or (key == 0xFFFFFFFFFFFFFFFF and (not in_lookup or offset == 0)):
                if in_lookup:
                    if key == in_key:
                        return {"out_status": 0, "out_value": (entry >> 64) & 0xFFFFFFFFFFFFFFFF, "out_slot": slot}
                    return {"out_status": 1, "out_value": 0, "out_slot": 0}
                self.table[slot] = in_key | (in_value << 64)
                return {"out_status": 0, "out_value": in_value, "out_slot": slot}
        return {"out_status": 1 if in_lookup else 2, "out_value": 0, "out_slot": 0}
