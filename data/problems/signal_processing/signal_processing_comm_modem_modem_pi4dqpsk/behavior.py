def to_signed(word, width):
    word &= (1 << width) - 1
    return word - (1 << width) if word & (1 << (width - 1)) else word


def rotate(x, y, phase):
    # Positive scale is immaterial: odd phases omit a common 1/sqrt(2).
    if phase == 0:
        return x, y
    if phase == 1:
        return x - y, x + y
    if phase == 2:
        return -y, x
    if phase == 3:
        return -x - y, x - y
    if phase == 4:
        return -x, -y
    if phase == 5:
        return y - x, -x - y
    if phase == 6:
        return y, -x
    return x + y, y - x


def unit_component(value, norm2):
    # Exact nearest-integer Q30 normalization, ties away from zero.
    magnitude = -value if value < 0 else value
    numerator = (magnitude * magnitude) << 60
    q = 0
    for bit in range(30, -1, -1):
        trial = q | (1 << bit)
        if trial * trial * norm2 <= numerator:
            q = trial
    if 4 * numerator >= (2 * q + 1) * (2 * q + 1) * norm2:
        q += 1
    return -q if value < 0 else q


class signal_processing_comm_modem_modem_pi4dqpsk:
    def __init__(self):
        self.reset()

    def reset(self):
        self.anchor_i = 1
        self.anchor_q = 0
        self.phase = 0
        self.negative_pi = 0

    def process(self, in_mode, in_sym, in_i, in_q):
        if not (in_mode & 1):
            symbol = in_sym & 3
            increment = 1 if symbol == 0 else 3 if symbol == 1 else -1 if symbol == 2 else -3
            phase = (self.phase + increment) & 7
            x, y = rotate(self.anchor_i, self.anchor_q, phase)
            norm2 = x * x + y * y
            out_i = unit_component(x, norm2)
            out_q = unit_component(y, norm2)
            self.phase = phase
            self.negative_pi = int(y == 0 and x < 0 and increment < 0)
            return {"out_sym": 0, "out_i": out_i & 0xFFFFFFFF, "out_q": out_q & 0xFFFFFFFF}

        x, y = to_signed(in_i, 32), to_signed(in_q, 32)
        if x == 0 and y == 0:
            x = 1  # arg(0) is explicitly defined as zero.
        previous_i, previous_q = rotate(self.anchor_i, self.anchor_q, self.phase)
        dot = x * previous_i + y * previous_q
        cross = y * previous_i - x * previous_q
        if cross > 0:
            symbol = 1 if dot < 0 else 0
        elif cross < 0:
            symbol = 3 if dot < 0 else 2
        elif dot < 0:
            # Preserve the source's closed [-pi,+pi] wrap at antipodal points.
            positive_pi = self.negative_pi or y > 0 or (y == 0 and x < 0)
            symbol = 1 if positive_pi else 3
        else:
            symbol = 2
        self.anchor_i = x
        self.anchor_q = y
        self.phase = 0
        self.negative_pi = 0
        return {"out_sym": symbol, "out_i": 0, "out_q": 0}
