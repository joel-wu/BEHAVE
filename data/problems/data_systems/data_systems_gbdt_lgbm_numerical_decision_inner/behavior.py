def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class GBDTLGBMNumericalDecisionInner:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_fval,
        in_threshold_in_bin,
        in_decision_type,
        in_default_bin,
        in_max_bin,
        in_left_child,
        in_right_child,
    ):
        fval = to_unsigned(in_fval, 32)
        threshold_in_bin = to_unsigned(in_threshold_in_bin, 32)
        decision_type = to_unsigned(in_decision_type, 8)
        default_bin = to_unsigned(in_default_bin, 32)
        max_bin = to_unsigned(in_max_bin, 32)
        left_child = to_unsigned(in_left_child, 32)
        right_child = to_unsigned(in_right_child, 32)

        missing_type = (decision_type >> 2) & 0x3
        default_left = (decision_type >> 1) & 0x1

        if (missing_type == 1 and fval == default_bin) or (missing_type == 2 and fval == max_bin):
            next_node = left_child if default_left == 1 else right_child
        elif fval <= threshold_in_bin:
            next_node = left_child
        else:
            next_node = right_child

        return {"out_next_node": to_unsigned(next_node, 32)}