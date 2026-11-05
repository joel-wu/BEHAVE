def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class SpectralRolloff:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_roll_percent,
        in_S_0, in_S_1, in_S_2, in_S_3,
        in_S_4, in_S_5, in_S_6, in_S_7,
        in_S_8, in_S_9, in_S_10, in_S_11,
        in_S_12, in_S_13, in_S_14, in_S_15,
        in_freq_0, in_freq_1, in_freq_2, in_freq_3,
        in_freq_4, in_freq_5, in_freq_6, in_freq_7,
        in_freq_8, in_freq_9, in_freq_10, in_freq_11,
        in_freq_12, in_freq_13, in_freq_14, in_freq_15
    ):
        s0 = to_unsigned(in_S_0, 32)
        s1 = to_unsigned(in_S_1, 32)
        s2 = to_unsigned(in_S_2, 32)
        s3 = to_unsigned(in_S_3, 32)
        s4 = to_unsigned(in_S_4, 32)
        s5 = to_unsigned(in_S_5, 32)
        s6 = to_unsigned(in_S_6, 32)
        s7 = to_unsigned(in_S_7, 32)
        s8 = to_unsigned(in_S_8, 32)
        s9 = to_unsigned(in_S_9, 32)
        s10 = to_unsigned(in_S_10, 32)
        s11 = to_unsigned(in_S_11, 32)
        s12 = to_unsigned(in_S_12, 32)
        s13 = to_unsigned(in_S_13, 32)
        s14 = to_unsigned(in_S_14, 32)
        s15 = to_unsigned(in_S_15, 32)

        f0 = to_unsigned(in_freq_0, 32)
        f1 = to_unsigned(in_freq_1, 32)
        f2 = to_unsigned(in_freq_2, 32)
        f3 = to_unsigned(in_freq_3, 32)
        f4 = to_unsigned(in_freq_4, 32)
        f5 = to_unsigned(in_freq_5, 32)
        f6 = to_unsigned(in_freq_6, 32)
        f7 = to_unsigned(in_freq_7, 32)
        f8 = to_unsigned(in_freq_8, 32)
        f9 = to_unsigned(in_freq_9, 32)
        f10 = to_unsigned(in_freq_10, 32)
        f11 = to_unsigned(in_freq_11, 32)
        f12 = to_unsigned(in_freq_12, 32)
        f13 = to_unsigned(in_freq_13, 32)
        f14 = to_unsigned(in_freq_14, 32)
        f15 = to_unsigned(in_freq_15, 32)

        roll_percent = to_unsigned(in_roll_percent, 16)

        total_energy = (
            s0 + s1 + s2 + s3 + s4 + s5 + s6 + s7 +
            s8 + s9 + s10 + s11 + s12 + s13 + s14 + s15
        )
        threshold_num = roll_percent * total_energy

        cumulative = s0
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f0}

        cumulative += s1
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f1}

        cumulative += s2
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f2}

        cumulative += s3
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f3}

        cumulative += s4
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f4}

        cumulative += s5
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f5}

        cumulative += s6
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f6}

        cumulative += s7
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f7}

        cumulative += s8
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f8}

        cumulative += s9
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f9}

        cumulative += s10
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f10}

        cumulative += s11
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f11}

        cumulative += s12
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f12}

        cumulative += s13
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f13}

        cumulative += s14
        if (cumulative << 16) >= threshold_num:
            return {"out_rolloff": f14}

        return {"out_rolloff": f15}