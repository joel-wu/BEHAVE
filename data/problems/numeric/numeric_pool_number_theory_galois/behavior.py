def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def reverse_bits_5(v):
    return ((v & 0x01) << 4) | ((v & 0x02) << 2) | (v & 0x04) | ((v & 0x08) >> 2) | ((v & 0x10) >> 4)


def reverse_bits_4(v):
    return ((v & 0x01) << 3) | ((v & 0x02) << 1) | ((v & 0x04) >> 1) | ((v & 0x08) >> 3)


class GaloisPermutationCore:
    def __init__(self):
        self.reset()

    def reset(self):
        self.coeff_mem = [0] * 32

    def process(self, in_mode, in_base_addr, in_galois_elt, in_modulus):
        operand = [self.coeff_mem[to_unsigned(in_base_addr + i, 5)] for i in range(16)]
        result = [0] * 16

        if in_mode == 0:
            for i in range(16):
                index_raw = i * in_galois_elt
                index = index_raw & 15
                result_value = operand[i]
                if (index_raw >> 4) & 1:
                    if operand[i] == 0:
                        result_value = 0
                    else:
                        result_value = (in_modulus - operand[i]) & 0xFF
                result[index] = result_value
        else:
            for i in range(16):
                x = 16 + i
                reversed_x = reverse_bits_5(x)
                index_raw = (in_galois_elt * reversed_x) >> 1
                index = index_raw & 15
                source_index = reverse_bits_4(index)
                result[i] = operand[source_index]

        for i in range(16):
            self.coeff_mem[to_unsigned(in_base_addr + i, 5)] = to_unsigned(result[i], 8)

        return {"out_base_addr": to_unsigned(in_base_addr, 5)}
