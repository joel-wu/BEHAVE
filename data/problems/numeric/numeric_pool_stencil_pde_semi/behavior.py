def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def project_endpoint(lo, hi, index, stride, upper):
    if lo > hi or stride == 0:
        return -1 if upper else 0
    if stride > 0:
        numerator = hi - index if upper else lo - index
    else:
        numerator = lo - index if upper else hi - index
    return numerator // stride if upper else -((-numerator) // stride)


class SemiInterpAssembleBoxProjector:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_dim,
        in_p_stored_as_transpose,
        in_cdir,
        in_index_x,
        in_index_y,
        in_index_z,
        in_stride_x,
        in_stride_y,
        in_stride_z,
        in_fine_imin_x,
        in_fine_imin_y,
        in_fine_imin_z,
        in_fine_imax_x,
        in_fine_imax_y,
        in_fine_imax_z,
    ):
        in_dim = to_unsigned(in_dim, 2)
        in_p_stored_as_transpose = to_unsigned(in_p_stored_as_transpose, 1)
        in_cdir = to_unsigned(in_cdir, 2)

        in_index_x = to_signed(in_index_x, 32)
        in_index_y = to_signed(in_index_y, 32)
        in_index_z = to_signed(in_index_z, 32)
        in_stride_x = to_signed(in_stride_x, 32)
        in_stride_y = to_signed(in_stride_y, 32)
        in_stride_z = to_signed(in_stride_z, 32)
        in_fine_imin_x = to_signed(in_fine_imin_x, 32)
        in_fine_imin_y = to_signed(in_fine_imin_y, 32)
        in_fine_imin_z = to_signed(in_fine_imin_z, 32)
        in_fine_imax_x = to_signed(in_fine_imax_x, 32)
        in_fine_imax_y = to_signed(in_fine_imax_y, 32)
        in_fine_imax_z = to_signed(in_fine_imax_z, 32)

        ghost_lo_x = 1 if in_dim >= 1 else 0
        ghost_hi_x = 1 if in_dim >= 1 else 0
        ghost_lo_y = 1 if in_dim >= 2 else 0
        ghost_hi_y = 1 if in_dim >= 2 else 0
        ghost_lo_z = 1 if in_dim >= 3 else 0
        ghost_hi_z = 1 if in_dim >= 3 else 0

        if in_p_stored_as_transpose:
            if in_cdir == 0 and in_dim >= 1:
                ghost_lo_x = 2
                ghost_hi_x = 2
            elif in_cdir == 1 and in_dim >= 2:
                ghost_lo_y = 2
                ghost_hi_y = 2
            elif in_cdir == 2 and in_dim >= 3:
                ghost_lo_z = 2
                ghost_hi_z = 2

        grown_imin_x = in_fine_imin_x - ghost_lo_x
        grown_imax_x = in_fine_imax_x + ghost_hi_x
        grown_imin_y = in_fine_imin_y - ghost_lo_y
        grown_imax_y = in_fine_imax_y + ghost_hi_y
        grown_imin_z = in_fine_imin_z - ghost_lo_z
        grown_imax_z = in_fine_imax_z + ghost_hi_z

        proj_min_x = project_endpoint(grown_imin_x, grown_imax_x, in_index_x, in_stride_x, 0)
        proj_max_x = project_endpoint(grown_imin_x, grown_imax_x, in_index_x, in_stride_x, 1)
        proj_min_y = project_endpoint(grown_imin_y, grown_imax_y, in_index_y, in_stride_y, 0)
        proj_max_y = project_endpoint(grown_imin_y, grown_imax_y, in_index_y, in_stride_y, 1)
        proj_min_z = project_endpoint(grown_imin_z, grown_imax_z, in_index_z, in_stride_z, 0)
        proj_max_z = project_endpoint(grown_imin_z, grown_imax_z, in_index_z, in_stride_z, 1)

        return {
            "out_send_imin_x": to_unsigned(proj_min_x, 32),
            "out_send_imin_y": to_unsigned(proj_min_y, 32),
            "out_send_imin_z": to_unsigned(proj_min_z, 32),
            "out_send_imax_x": to_unsigned(proj_max_x, 32),
            "out_send_imax_y": to_unsigned(proj_max_y, 32),
            "out_send_imax_z": to_unsigned(proj_max_z, 32),
            "out_recv_imin_x": to_unsigned(proj_min_x, 32),
            "out_recv_imin_y": to_unsigned(proj_min_y, 32),
            "out_recv_imin_z": to_unsigned(proj_min_z, 32),
            "out_recv_imax_x": to_unsigned(proj_max_x, 32),
            "out_recv_imax_y": to_unsigned(proj_max_y, 32),
            "out_recv_imax_z": to_unsigned(proj_max_z, 32),
            "out_sendr_imin_x": to_unsigned(proj_min_x, 32),
            "out_sendr_imin_y": to_unsigned(proj_min_y, 32),
            "out_sendr_imin_z": to_unsigned(proj_min_z, 32),
            "out_sendr_imax_x": to_unsigned(proj_max_x, 32),
            "out_sendr_imax_y": to_unsigned(proj_max_y, 32),
            "out_sendr_imax_z": to_unsigned(proj_max_z, 32),
            "out_recvr_imin_x": to_unsigned(proj_min_x, 32),
            "out_recvr_imin_y": to_unsigned(proj_min_y, 32),
            "out_recvr_imin_z": to_unsigned(proj_min_z, 32),
            "out_recvr_imax_x": to_unsigned(proj_max_x, 32),
            "out_recvr_imax_y": to_unsigned(proj_max_y, 32),
            "out_recvr_imax_z": to_unsigned(proj_max_z, 32),
        }
