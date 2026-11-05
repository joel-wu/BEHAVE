def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class AiMlAudioRnntRnntJointNetwork:
    def __init__(self, encoding_dim=4, output_dim=4):
        self.encoding_dim = encoding_dim
        self.output_dim = output_dim
        self.reset()

    def reset(self):
        return None

    def process(
        self,
        in_source_encodings,
        in_target_encodings,
        in_source_length,
        in_target_length,
        in_weight,
        in_bias,
    ):
        act = []
        for i in range(self.encoding_dim):
            source_i = to_signed((in_source_encodings >> (16 * i)) & 0xFFFF, 16)
            target_i = to_signed((in_target_encodings >> (16 * i)) & 0xFFFF, 16)
            joint_i = source_i + target_i
            act.append(0 if joint_i < 0 else joint_i)

        out_output = 0
        for o in range(self.output_dim):
            sum_o = to_signed((in_bias >> (16 * o)) & 0xFFFF, 16)
            for i in range(self.encoding_dim):
                weight_o_i = to_signed((in_weight >> (16 * (o * self.encoding_dim + i))) & 0xFFFF, 16)
                sum_o = sum_o + act[i] * weight_o_i
            out_output |= to_unsigned(to_signed(sum_o, 16), 16) << (16 * o)

        return {
            "out_output": to_unsigned(out_output, 16 * self.output_dim),
            "out_source_length": to_unsigned(in_source_length, 16),
            "out_target_length": to_unsigned(in_target_length, 16),
        }
