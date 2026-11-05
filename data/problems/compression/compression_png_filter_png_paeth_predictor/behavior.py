def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CompressionPngFilterPngPaethPredictor:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_filtered_byte, in_left_byte, in_up_byte, in_up_left_byte):
        a = in_left_byte
        b = in_up_byte
        c = in_up_left_byte
        x = in_filtered_byte

        p = a + b - c
        pa0 = p - a
        pb0 = p - b
        pc0 = p - c
        pa = pa0 if pa0 >= 0 else -pa0
        pb = pb0 if pb0 >= 0 else -pb0
        pc = pc0 if pc0 >= 0 else -pc0

        if pa <= pb and pa <= pc:
            pr = a
        elif pb <= pc:
            pr = b
        else:
            pr = c

        recon = x + pr

        return {
            "out_recon_byte": to_unsigned(recon, 8),
            "out_predictor_byte": to_unsigned(pr, 8),
        }