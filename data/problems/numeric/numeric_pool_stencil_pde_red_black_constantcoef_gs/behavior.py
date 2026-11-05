def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def trunc_div(a, b):
    if b == 0:
        return 0
    neg = (a < 0) ^ (b < 0)
    aa = -a if a < 0 else a
    bb = -b if b < 0 else b
    q = aa // bb
    return -q if neg else q


class RedBlackStencilPointUpdate:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_valid = 0
        self.out_active = 0
        self.out_x_new = 0
        self.out_rb_phase_next = 0

    def process(
        self,
        in_zero_guess,
        in_phase_mode,
        in_rb_phase,
        in_ndim,
        in_stencil_size,
        in_constant_diag,
        in_b,
        in_xm,
        in_xp,
        in_ym,
        in_yp,
        in_zm,
        in_zp,
        in_app0,
        in_app1,
        in_app2,
        in_app3,
        in_app4,
        in_app5,
        in_ap_const,
        in_ap_var,
        in_coord_x,
        in_coord_y,
        in_coord_z,
    ):
        rb_phase = in_rb_phase & 1
        ndim = in_ndim & 0x3
        stencil_size = in_stencil_size & 0x7
        coord_x = in_coord_x & 0xFFFF
        coord_y = in_coord_y & 0xFFFF
        coord_z = in_coord_z & 0xFFFF

        if ndim == 0 or ndim == 1:
            coord_sum = coord_x
        elif ndim == 2:
            coord_sum = coord_x + coord_y
        else:
            coord_sum = coord_x + coord_y + coord_z

        active = ((rb_phase + coord_sum) & 1) == 0
        rb_phase_next = rb_phase ^ 1

        if not active:
            x_new = 0
        else:
            diag = to_signed(in_ap_const, 32) if (in_constant_diag & 1) else to_signed(in_ap_var, 32)
            b_val = to_signed(in_b, 32)
            phase_mode = in_phase_mode & 0x3
            if phase_mode == 0:
                x_new = trunc_div(b_val, diag)
            else:
                app0 = to_signed(in_app0, 32)
                app1 = to_signed(in_app1, 32)
                app2 = to_signed(in_app2, 32)
                app3 = to_signed(in_app3, 32)
                app4 = to_signed(in_app4, 32)
                app5 = to_signed(in_app5, 32)
                xm = to_signed(in_xm, 32)
                xp = to_signed(in_xp, 32)
                ym = to_signed(in_ym, 32)
                yp = to_signed(in_yp, 32)
                zm = to_signed(in_zm, 32)
                zp = to_signed(in_zp, 32)

                if stencil_size == 5:
                    neighbor_sum = app0 * xm + app1 * xp + app2 * ym + app3 * yp
                elif stencil_size == 7:
                    neighbor_sum = app0 * xm + app1 * xp + app2 * ym + app3 * yp + app4 * zm + app5 * zp
                else:
                    neighbor_sum = app0 * xm + app1 * xp

                numerator = b_val - neighbor_sum
                x_new = trunc_div(numerator, diag)

        self.out_valid = 1
        self.out_active = 1 if active else 0
        self.out_x_new = to_unsigned(x_new, 32)
        self.out_rb_phase_next = rb_phase_next

        return {
            "out_active": self.out_active,
            "out_x_new": self.out_x_new,
            "out_rb_phase_next": self.out_rb_phase_next,
        }