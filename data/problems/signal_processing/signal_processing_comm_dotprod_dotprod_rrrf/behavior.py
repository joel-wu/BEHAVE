from fp_lib import fp32_add, fp32_mul


class signal_processing_comm_dotprod_dotprod_rrrf:
    def __init__(self):
        self.reset()

    def reset(self):
        self.running_sum = 0x00000000
        self.active = 0

    def process(self, in_x, in_h, in_last):
        product = fp32_mul(in_x, in_h)
        next_sum = product if self.active == 0 else fp32_add(self.running_sum, product)
        self.running_sum = next_sum
        self.active = 1
        if in_last:
            self.running_sum = 0x00000000
            self.active = 0
            return {"out_y": next_sum & 0xFFFFFFFF}
        return None