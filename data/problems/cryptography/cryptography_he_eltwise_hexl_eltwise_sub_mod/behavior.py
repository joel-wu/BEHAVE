def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CryptographyHeEltwiseHexlEltwiseSubMod:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_operand1, in_operand2, in_modulus):
        operand1 = to_unsigned(in_operand1, 64)
        operand2 = to_unsigned(in_operand2, 64)
        modulus = to_unsigned(in_modulus, 64)
        if operand1 >= operand2:
            out_result = operand1 - operand2
        else:
            out_result = operand1 + modulus - operand2
        return {"out_result": to_unsigned(out_result, 64)}