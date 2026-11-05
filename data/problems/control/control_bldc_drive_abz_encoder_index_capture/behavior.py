class EncoderIndexLifecycle:
    def __init__(self):
        self.reset()

    def reset(self):
        self.circular_count = 0
        self.linear_count = 0
        self.is_ready = 0
        self.index_found = 0
        self.subscribed = 0
        self.anticogging_valid = 0

    def process(self, in_op, in_use_index, in_lockin_only, in_override_enable,
                in_use_index_offset, in_pre_calibrated, in_anticogging_pre_calibrated,
                in_offset_count, in_circular_count, in_linear_count):
        op = in_op & 3
        if op == 1:
            self.subscribed = int(bool(in_use_index & 1) and
                                  (bool(in_override_enable & 1) or not bool(in_lockin_only & 1)))
        elif op == 2:
            self.circular_count = in_circular_count & 0xffffffff
            self.linear_count = in_linear_count & 0xffffffff
        elif op == 3 and self.subscribed:
            if in_use_index & 1:
                self.circular_count = 0
                if in_use_index_offset & 1:
                    self.linear_count = in_offset_count & 0xffffffff
                self.is_ready = in_pre_calibrated & 1
                if (in_pre_calibrated & 1) and (in_anticogging_pre_calibrated & 1):
                    self.anticogging_valid = 1
                self.index_found = 1
            self.subscribed = 0
        return {"out_circular_count": self.circular_count,
                "out_linear_count": self.linear_count,
                "out_is_ready": self.is_ready,
                "out_index_found": self.index_found,
                "out_subscribed": self.subscribed,
                "out_anticogging_valid": self.anticogging_valid}
