def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CryptographyHeEltwiseHexlEltwiseAddMod:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_operand1, in_operand2, in_modulus):
        operand1 = to_unsigned(in_operand1, 64)
        operand2 = to_unsigned(in_operand2, 64)
        modulus = to_unsigned(in_modulus, 64)
        sum_ = to_unsigned(operand1 + operand2, 64)
        if modulus != 0 and sum_ >= modulus:
            result = sum_ - modulus
        else:
            result = sum_
        return {"out_result": to_unsigned(result, 64)}