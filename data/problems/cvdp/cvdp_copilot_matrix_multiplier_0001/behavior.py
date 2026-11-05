class MatrixMultiplier:
    def __init__(self, ROW_A=4, COL_A=4, ROW_B=4, COL_B=4, INPUT_DATA_WIDTH=8, OUTPUT_DATA_WIDTH=None):
        self.ROW_A = ROW_A
        self.COL_A = COL_A
        self.ROW_B = ROW_B
        self.COL_B = COL_B
        self.INPUT_DATA_WIDTH = INPUT_DATA_WIDTH
        self.OUTPUT_DATA_WIDTH = 2 * INPUT_DATA_WIDTH + (COL_A - 1).bit_length() if OUTPUT_DATA_WIDTH is None else OUTPUT_DATA_WIDTH

    def reset(self):
        pass

    def process(self, matrix_a, matrix_b):
        matrix_c = 0
        mask = (1 << self.INPUT_DATA_WIDTH) - 1
        for r in range(self.ROW_A):
            for c in range(self.COL_B):
                total = 0
                for k in range(self.COL_A):
                    a = (matrix_a >> ((r * self.COL_A + k) * self.INPUT_DATA_WIDTH)) & mask
                    b = (matrix_b >> ((k * self.COL_B + c) * self.INPUT_DATA_WIDTH)) & mask
                    total += a * b
                matrix_c |= total << ((r * self.COL_B + c) * self.OUTPUT_DATA_WIDTH)
        return {"matrix_c": matrix_c}
