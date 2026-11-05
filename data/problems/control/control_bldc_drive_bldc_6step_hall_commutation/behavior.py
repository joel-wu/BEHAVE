def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


TRAP_120_MAP = {
    0: (0, 1, -1),
    1: (-1, 1, 0),
    2: (-1, 0, 1),
    3: (0, -1, 1),
    4: (1, -1, 0),
    5: (1, 0, -1),
}

TRAP_150_MAP = {
    0: (0, 1, -1),
    1: (-1, 1, -1),
    2: (-1, 1, 0),
    3: (-1, 1, 1),
    4: (-1, 0, 1),
    5: (-1, -1, 1),
    6: (0, -1, 1),
    7: (1, -1, 1),
    8: (1, -1, 0),
    9: (1, -1, -1),
    10: (1, 0, -1),
    11: (1, 1, -1),
}


class ControlBldcDriveBldc6stepHallCommutation:
    def __init__(self):
        self.reset()

    def reset(self):
        self.idle = 0

    def process(self, in_uq, in_voltage_limit, in_angle_el, in_mode_150, in_modulation_centered):
        uq = to_signed(in_uq, 16)
        voltage_limit = to_signed(in_voltage_limit, 16)
        angle = to_unsigned(in_angle_el, 16)

        center = (voltage_limit >> 1) if in_modulation_centered else uq
        angle_off = (angle + 0x1555) & 0xFFFF

        if in_mode_150:
            sector = (angle_off * 12) >> 16
            ma, mb, mc = TRAP_150_MAP[sector]
        else:
            sector = (angle_off * 6) >> 16
            ma, mb, mc = TRAP_120_MAP[sector]

        va = center if ma == 0 else (center + uq if ma > 0 else center - uq)
        vb = center if mb == 0 else (center + uq if mb > 0 else center - uq)
        vc = center if mc == 0 else (center + uq if mc > 0 else center - uq)

        va = 32767 if va > 32767 else (-32768 if va < -32768 else va)
        vb = 32767 if vb > 32767 else (-32768 if vb < -32768 else vb)
        vc = 32767 if vc > 32767 else (-32768 if vc < -32768 else vc)

        return {
            "out_ua": to_unsigned(va, 16),
            "out_ub": to_unsigned(vb, 16),
            "out_uc": to_unsigned(vc, 16),
            "out_phase_a_en": 0 if ma == 0 else 1,
            "out_phase_b_en": 0 if mb == 0 else 1,
            "out_phase_c_en": 0 if mc == 0 else 1,
        }