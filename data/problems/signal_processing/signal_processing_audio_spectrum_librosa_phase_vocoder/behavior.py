ATAN = (536870912, 316933406, 167458907, 85004756, 42667331, 21354465, 10679838, 5340245, 2670163, 1335087, 667544, 333772, 166886, 83443, 41722, 20861, 10430, 5215, 2608, 1304, 652, 326, 163, 81, 41, 20, 10, 5, 3, 1)
GAIN_INV = 652032874


def signed(value, width):
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


def magnitude(x, y):
    square = (x*x + y*y) << 32
    root = 0
    for bit in range(31, -1, -1):
        trial = root | (1 << bit)
        if trial*trial <= square:
            root = trial
    return root


def angle(x, y):
    if y == 0:
        return 0 if x >= 0 else 1 << 31
    if x == 0:
        return (1 << 30) if y > 0 else -(1 << 30)
    phase = 0
    if x < 0:
        phase = (1 << 31) if y > 0 else -(1 << 31)
        x, y = -x, -y
    x <<= 24
    y <<= 24
    for i in range(30):
        old_x = x
        if y >= 0:
            x += y >> i
            y -= old_x >> i
            phase += ATAN[i]
        else:
            x -= y >> i
            y += old_x >> i
            phase -= ATAN[i]
    return phase


def rotate(mag, phase):
    x = (mag * GAIN_INV) >> 30
    y = 0
    z = signed(phase, 32)
    if z > (1 << 30):
        x = -x
        z -= 1 << 31
    elif z < -(1 << 30):
        x = -x
        z += 1 << 31
    for i in range(30):
        old_x = x
        if z >= 0:
            x -= y >> i
            y += old_x >> i
            z -= ATAN[i]
        else:
            x += y >> i
            y -= old_x >> i
            z += ATAN[i]
    return (x + 32768) >> 16, (y + 32768) >> 16


class SignalProcessingAudioSpectrumLibrosaPhaseVocoder:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_c0_real, in_c0_imag, in_c1_real, in_c1_imag,
                in_phase_acc, in_alpha, in_phi_advance):
        x0, y0 = signed(in_c0_real, 16), signed(in_c0_imag, 16)
        x1, y1 = signed(in_c1_real, 16), signed(in_c1_imag, 16)
        alpha = min(in_alpha & 131071, 65536)
        mag = ((65536-alpha)*magnitude(x0, y0) + alpha*magnitude(x1, y1)) >> 16
        phase = signed(in_phase_acc, 64)
        advance = signed(in_phi_advance, 64)
        real, imag = rotate(mag, phase)
        delta = angle(x1, y1) - angle(x0, y0) - advance
        turns = delta >> 32
        fraction = delta - (turns << 32)
        if fraction > (1 << 31) or (fraction == (1 << 31) and (turns & 1)):
            turns += 1
        residual = delta - (turns << 32)
        return {"out_real": real & 131071, "out_imag": imag & 131071,
                "out_phase_acc": (phase + advance + residual) & ((1 << 64)-1)}
