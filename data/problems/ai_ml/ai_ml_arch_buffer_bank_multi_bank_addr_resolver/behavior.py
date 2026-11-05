def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlArchBufferBankMultiBankAddrResolver:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_laddr):
        row = in_laddr & 0x03FF
        bank = (in_laddr >> 10) & 0x07
        return {
            "out_bank": to_unsigned(bank, 8),
            "out_row": to_unsigned(row, 16),
        }