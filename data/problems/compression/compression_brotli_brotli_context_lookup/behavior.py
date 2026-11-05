# Brotli context-table data: Brotli Authors, MIT (see LICENSE.MIT).
UTF8_LAST = (
    0, 0, 0, 0, 0, 0, 0, 0, 0, 4, 4, 0, 0, 4, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    8, 12, 16, 12, 12, 20, 12, 16, 24, 28, 12, 12, 32, 12, 36, 12,
    44, 44, 44, 44, 44, 44, 44, 44, 44, 44, 32, 32, 24, 40, 28, 12,
    12, 48, 52, 52, 52, 48, 52, 52, 52, 48, 52, 52, 52, 52, 52, 48,
    52, 52, 52, 52, 52, 48, 52, 52, 52, 52, 52, 24, 12, 28, 12, 12,
    12, 56, 60, 60, 60, 56, 60, 60, 60, 56, 60, 60, 60, 60, 60, 56,
    60, 60, 60, 60, 60, 56, 60, 60, 60, 60, 60, 24, 12, 28, 12, 0,
)
UTF8_PREVIOUS = (
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1,
    1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2,
    2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1,
    1, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3,
    3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 1, 1, 1, 1, 0,
)


def signed_class(byte):
    if byte == 0:
        return 0
    if byte < 16:
        return 1
    if byte < 64:
        return 2
    if byte < 128:
        return 3
    if byte < 192:
        return 4
    if byte < 240:
        return 5
    if byte < 255:
        return 6
    return 7


class BrotliContextLookup:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_mode, in_p1, in_p2):
        if in_mode == 0:
            context = in_p1 & 63
        elif in_mode == 1:
            context = in_p1 >> 2
        elif in_mode == 2:
            last = (UTF8_LAST[in_p1] if in_p1 < 128 else
                    (in_p1 & 1) + (0 if in_p1 < 192 else 2))
            previous = (UTF8_PREVIOUS[in_p2] if in_p2 < 128 else
                        (0 if in_p2 < 224 else 2))
            context = last | previous
        else:
            context = (signed_class(in_p1) << 3) | signed_class(in_p2)
        return {"out_context": context}
