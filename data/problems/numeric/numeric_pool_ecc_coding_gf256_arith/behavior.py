def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class XorSummer64x4:
    def __init__(self):
        self.reset()

    def reset(self):
        self.dest_word = 0
        self.waiting_valid = 0
        self.waiting_word = 0

    def process(self, in_finalize, in_dest_init, in_dest_word,
                in_src_count, in_src0_word, in_src1_word,
                in_src2_word, in_src3_word):
        dest_word = self.dest_word
        waiting_valid = self.waiting_valid
        waiting_word = self.waiting_word

        if in_dest_init:
            dest_word = to_unsigned(in_dest_word, 64)
            waiting_valid = 0
            waiting_word = 0

        if in_src_count >= 1:
            source_word = to_unsigned(in_src0_word, 64)
            if waiting_valid:
                dest_word = to_unsigned(dest_word ^ waiting_word ^ source_word, 64)
                waiting_valid = 0
            else:
                waiting_word = source_word
                waiting_valid = 1

        if in_src_count >= 2:
            source_word = to_unsigned(in_src1_word, 64)
            if waiting_valid:
                dest_word = to_unsigned(dest_word ^ waiting_word ^ source_word, 64)
                waiting_valid = 0
            else:
                waiting_word = source_word
                waiting_valid = 1

        if in_src_count >= 3:
            source_word = to_unsigned(in_src2_word, 64)
            if waiting_valid:
                dest_word = to_unsigned(dest_word ^ waiting_word ^ source_word, 64)
                waiting_valid = 0
            else:
                waiting_word = source_word
                waiting_valid = 1

        if in_src_count >= 4:
            source_word = to_unsigned(in_src3_word, 64)
            if waiting_valid:
                dest_word = to_unsigned(dest_word ^ waiting_word ^ source_word, 64)
                waiting_valid = 0
            else:
                waiting_word = source_word
                waiting_valid = 1

        if in_finalize and waiting_valid:
            dest_word = to_unsigned(dest_word ^ waiting_word, 64)
            waiting_valid = 0

        self.dest_word = to_unsigned(dest_word, 64)
        self.waiting_valid = to_unsigned(waiting_valid, 1)
        self.waiting_word = to_unsigned(waiting_word, 64)

        return {
            "out_dest_word": self.dest_word,
            "out_waiting_valid": self.waiting_valid,
            "out_waiting_word": self.waiting_word,
        }
