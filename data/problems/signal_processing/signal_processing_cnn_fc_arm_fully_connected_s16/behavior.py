def signed(value, width):
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


class SignalProcessingCnnFcArmFullyConnectedS16:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_batches, in_depth, in_outputs, in_input, in_kernel,
                in_bias, in_multiplier, in_shift, in_activation_min, in_activation_max):
        batches = in_batches & 3
        depth = in_depth & 15
        outputs = in_outputs & 7
        shift = signed(in_shift, 6)
        lo = signed(in_activation_min, 16)
        hi = signed(in_activation_max, 16)
        if not (1 <= batches <= 2 and 1 <= depth <= 8 and 1 <= outputs <= 4):
            return {"out_output": 0, "out_error": 1}
        if shift < -31 or shift > 7 or lo > hi:
            return {"out_output": 0, "out_error": 1}

        multiplier = in_multiplier & 0x7fffffff
        reduced = min((multiplier + 32768) >> 16, 32767)
        packed = 0
        for batch in range(batches):
            for row in range(outputs):
                acc = signed(in_bias >> (64 * row), 64)
                for col in range(depth):
                    activation = signed(in_input >> (16 * (batch * depth + col)), 16)
                    weight = signed(in_kernel >> (8 * (row * depth + col)), 8)
                    acc += activation * weight
                # Integer widening preserves the quantizer before activation clamp.
                value = (((acc * reduced) >> (14 - shift)) + 1) >> 1
                value = min(hi, max(lo, value))
                packed |= (value & 65535) << (16 * (batch * outputs + row))
        return {"out_output": packed, "out_error": 0}
