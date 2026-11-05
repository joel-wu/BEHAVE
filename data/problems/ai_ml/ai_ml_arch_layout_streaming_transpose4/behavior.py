class StreamingTranspose4:
    def __init__(self):
        self.reset()

    def reset(self):
        self.rows = [0] * 4
        self.row_index = 0

    def process(self, in_row):
        self.rows[self.row_index] = in_row & 0xFFFFFFFFFFFFFFFF
        self.row_index += 1
        result = []
        if self.row_index == 4:
            self.row_index = 0
            for column in range(4):
                packed = 0
                for row in range(4):
                    element = (self.rows[row] >> (16 * column)) & 0xFFFF
                    packed |= element << (16 * row)
                result.append({"out_col": packed})
        return result
