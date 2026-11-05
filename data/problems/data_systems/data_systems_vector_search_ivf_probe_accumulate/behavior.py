def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class DataSystemsVectorSearchIvfProbeAccumulate:
    def __init__(self):
        self.reset()

    def reset(self):
        self.nlist_visited_accum = 0
        self.ndis_accum = 0

    def process(self, in_key, in_list_empty, in_scan_count, in_last_probe):
        nlist_visited_accum = self.nlist_visited_accum
        ndis_accum = self.ndis_accum

        key_s = to_signed(in_key, 32)

        if key_s < 0 or in_list_empty:
            visited_inc = 0
            ndis_inc = 0
        else:
            visited_inc = 1
            ndis_inc = to_unsigned(in_scan_count, 32)

        nlist_visited_next = to_unsigned(nlist_visited_accum + visited_inc, 32)
        ndis_next = to_unsigned(ndis_accum + ndis_inc, 32)

        if in_last_probe:
            self.nlist_visited_accum = 0
            self.ndis_accum = 0
            return {
                "out_nlist_visited": to_unsigned(nlist_visited_next, 32),
                "out_ndis": to_unsigned(ndis_next, 32),
            }

        self.nlist_visited_accum = nlist_visited_next
        self.ndis_accum = ndis_next
        return None