SILU_GRID = (0, 2112, 4352, 6718, 9211, 11827, 14565, 17423, 20397, 23484, 26679, 29981, 33383, 36882, 40473, 44150, 47911, 51748, 55658, 59636, 63676, 67775, 71926, 76126, 80371, 84655, 88976, 93328, 97709, 102114, 106541, 110987, 115448, 119922, 124406, 128898, 133396, 137898, 142403, 146907, 151411, 155913, 160412, 164906, 169395, 173878, 178354, 182823, 187284, 191736, 196180, 200616, 205042, 209458, 213866, 218264, 222652, 227032, 231401, 235762, 240113, 244455, 248789, 253113, 257429, 261737, 266036, 270327, 274611, 278887, 283156, 287417, 291672, 295920, 300161, 304397, 308626, 312849, 317067, 321280, 325487, 329689, 333887, 338080, 342268, 346452, 350632, 354808, 358981, 363150, 367315, 371477, 375636, 379792, 383946, 388096, 392244, 396389, 400532, 404672, 408811, 412947, 417081, 421214, 425345, 429473, 433601, 437726, 441851, 445973, 450095, 454215, 458334, 462452, 466569, 470684, 474799, 478912, 483025, 487137, 491248, 495359, 499468, 503577, 507685, 511793, 515900, 520006, 524112)



def signed16(word):
    value = word & 65535
    return value - 65536 if value >= 32768 else value


def round_even(value, divisor):
    magnitude = -value if value < 0 else value
    quotient = magnitude // divisor
    remainder = magnitude % divisor
    if 2 * remainder > divisor or (2 * remainder == divisor and quotient % 2 == 1):
        quotient += 1
    return -quotient if value < 0 else quotient


def sat16(value):
    return -32768 if value < -32768 else (32767 if value > 32767 else value)


def silu(value):
    magnitude = -value if value < 0 else value
    positive = magnitude
    if magnitude < 2048:
        index = magnitude // 16
        residual = magnitude % 16
        numerator = (16 - residual) * SILU_GRID[index] + residual * SILU_GRID[index + 1]
        positive = round_even(numerator, 4096)
    return positive - magnitude if value < 0 else positive


class AiMlTxMlpSwigluLlamaMlpSwiglu:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x, in_gate_weight, in_up_weight, in_down_weight,
                in_gate_bias, in_up_bias, in_down_bias):
        hidden = [0] * 8
        for j in range(8):
            gate_sum = signed16(in_gate_bias >> (16 * j)) * 256
            up_sum = signed16(in_up_bias >> (16 * j)) * 256
            for i in range(4):
                x = signed16(in_x >> (16 * i))
                gate_sum += x * signed16(in_gate_weight >> (16 * (4 * j + i)))
                up_sum += x * signed16(in_up_weight >> (16 * (4 * j + i)))
            gate = sat16(round_even(gate_sum, 256))
            up = sat16(round_even(up_sum, 256))
            hidden[j] = sat16(round_even(silu(gate) * up, 256))
        result = 0
        for k in range(4):
            total = signed16(in_down_bias >> (16 * k)) * 256
            for j in range(8):
                total += hidden[j] * signed16(in_down_weight >> (16 * (8 * k + j)))
            result |= (sat16(round_even(total, 256)) & 65535) << (16 * k)
        return {"out_y": result}
