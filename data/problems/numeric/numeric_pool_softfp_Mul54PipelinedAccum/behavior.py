def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class Mul54PipelinedAccum:
    def __init__(self):
        self.reset()

    def reset(self):
        self.val_s1 = 0
        self.val_s2 = 0
        self.reg_a_s1 = 0
        self.reg_b_s1 = 0
        self.reg_a_s2 = 0
        self.reg_b_s2 = 0
        self.reg_result_s3 = 0

    def process(
        self,
        in_val_s0,
        in_latch_a_s0,
        in_a_s0,
        in_latch_b_s0,
        in_b_s0,
        in_c_s2,
    ):
        val_s1 = self.val_s1
        val_s2 = self.val_s2
        reg_a_s1 = self.reg_a_s1
        reg_b_s1 = self.reg_b_s1
        reg_a_s2 = self.reg_a_s2
        reg_b_s2 = self.reg_b_s2
        reg_result_s3 = self.reg_result_s3

        val_s1_next = to_unsigned(in_val_s0, 1)
        val_s2_next = val_s1

        reg_a_s1_next = reg_a_s1
        reg_b_s1_next = reg_b_s1
        if in_val_s0:
            if in_latch_a_s0:
                reg_a_s1_next = to_unsigned(in_a_s0, 54)
            if in_latch_b_s0:
                reg_b_s1_next = to_unsigned(in_b_s0, 54)

        reg_a_s2_next = reg_a_s2
        reg_b_s2_next = reg_b_s2
        if val_s1:
            reg_a_s2_next = reg_a_s1
            reg_b_s2_next = reg_b_s1

        reg_result_s3_next = reg_result_s3
        out = None
        if val_s2:
            product_lo = to_unsigned(reg_a_s2 * reg_b_s2, 105)
            reg_result_s3_next = to_unsigned(product_lo + to_unsigned(in_c_s2, 105), 105)
            out = {"out_result_s3": reg_result_s3_next}

        self.val_s1 = val_s1_next
        self.val_s2 = val_s2_next
        self.reg_a_s1 = reg_a_s1_next
        self.reg_b_s1 = reg_b_s1_next
        self.reg_a_s2 = reg_a_s2_next
        self.reg_b_s2 = reg_b_s2_next
        self.reg_result_s3 = reg_result_s3_next

        return out