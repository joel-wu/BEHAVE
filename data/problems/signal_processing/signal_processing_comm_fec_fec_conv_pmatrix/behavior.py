PUNCTURE_MATRICES = {
    0: {
        0: [1, 1, 1, 0],
        1: [1, 1, 0, 1, 0, 1],
        2: [1, 1, 1, 1, 1, 0, 0, 0],
        3: [1, 1, 0, 1, 0, 1, 0, 1, 0, 1],
        4: [1, 1, 1, 0, 1, 0, 1, 0, 0, 1, 0, 1],
        5: [1, 1, 1, 1, 0, 1, 0, 1, 0, 0, 0, 1, 0, 1],
    },
    1: {
        0: [1, 1, 1, 0],
        1: [1, 1, 1, 1, 0, 0],
        2: [1, 1, 0, 1, 1, 0, 1, 0],
        3: [1, 0, 1, 1, 0, 1, 1, 0, 0, 1],
        4: [1, 1, 0, 1, 1, 0, 1, 0, 1, 0, 0, 1],
        5: [1, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 0, 0],
    },
}


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class SignalProcessingCommFecFecConvPmatrix:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_k_sel, in_rate_sel, in_index):
        k_sel = to_unsigned(in_k_sel, 1)
        rate_sel = to_unsigned(in_rate_sel, 3)
        index = to_unsigned(in_index, 4)
        family = PUNCTURE_MATRICES.get(k_sel, None)
        matrix = None if family is None else family.get(rate_sel, None)
        if matrix is None:
            return {"out_found": 0, "out_bit": 0}
        if index < len(matrix):
            return {"out_found": 1, "out_bit": to_unsigned(matrix[index], 1)}
        return {"out_found": 0, "out_bit": 0}