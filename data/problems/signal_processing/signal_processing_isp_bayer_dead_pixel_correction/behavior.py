def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class BayerDeadPixelCorrection:
    def __init__(self, PIXEL_W=16, THRESH_W=16):
        self.pixel_width = PIXEL_W
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_center,
        in_top_left,
        in_top_mid,
        in_top_right,
        in_mid_left,
        in_mid_right,
        in_bottom_left,
        in_bottom_mid,
        in_bottom_right,
        in_threshold,
    ):
        center = in_center
        top_left = in_top_left
        top_mid = in_top_mid
        top_right = in_top_right
        mid_left = in_mid_left
        mid_right = in_mid_right
        bottom_left = in_bottom_left
        bottom_mid = in_bottom_mid
        bottom_right = in_bottom_right
        threshold = in_threshold

        neighbor_min = top_left
        if top_mid < neighbor_min:
            neighbor_min = top_mid
        if top_right < neighbor_min:
            neighbor_min = top_right
        if mid_left < neighbor_min:
            neighbor_min = mid_left
        if mid_right < neighbor_min:
            neighbor_min = mid_right
        if bottom_left < neighbor_min:
            neighbor_min = bottom_left
        if bottom_mid < neighbor_min:
            neighbor_min = bottom_mid
        if bottom_right < neighbor_min:
            neighbor_min = bottom_right

        neighbor_max = top_left
        if top_mid > neighbor_max:
            neighbor_max = top_mid
        if top_right > neighbor_max:
            neighbor_max = top_right
        if mid_left > neighbor_max:
            neighbor_max = mid_left
        if mid_right > neighbor_max:
            neighbor_max = mid_right
        if bottom_left > neighbor_max:
            neighbor_max = bottom_left
        if bottom_mid > neighbor_max:
            neighbor_max = bottom_mid
        if bottom_right > neighbor_max:
            neighbor_max = bottom_right

        cond1 = (center <= neighbor_min) or (center >= neighbor_max)

        d0 = center - top_left if center >= top_left else top_left - center
        d1 = center - top_mid if center >= top_mid else top_mid - center
        d2 = center - top_right if center >= top_right else top_right - center
        d3 = center - mid_left if center >= mid_left else mid_left - center
        d4 = center - mid_right if center >= mid_right else mid_right - center
        d5 = center - bottom_left if center >= bottom_left else bottom_left - center
        d6 = center - bottom_mid if center >= bottom_mid else bottom_mid - center
        d7 = center - bottom_right if center >= bottom_right else bottom_right - center

        cond2 = (
            (d0 > threshold)
            and (d1 > threshold)
            and (d2 > threshold)
            and (d3 > threshold)
            and (d4 > threshold)
            and (d5 > threshold)
            and (d6 > threshold)
            and (d7 > threshold)
        )

        is_dead = cond1 and cond2

        if not is_dead:
            return {"out_pixel": to_unsigned(center, self.pixel_width), "out_is_dead": 0}

        vtmp = (center << 1) - top_mid - bottom_mid
        vertical_grad = vtmp if vtmp >= 0 else -vtmp

        htmp = (center << 1) - mid_left - mid_right
        horizontal_grad = htmp if htmp >= 0 else -htmp

        ltmp = (center << 1) - top_left - bottom_right
        left_diagonal_grad = ltmp if ltmp >= 0 else -ltmp

        rtmp = (center << 1) - top_right - bottom_left
        right_diagonal_grad = rtmp if rtmp >= 0 else -rtmp

        mean_vertical = (top_mid + bottom_mid) >> 1
        mean_horizontal = (mid_left + mid_right) >> 1
        mean_left_diagonal = (top_left + bottom_right) >> 1
        mean_right_diagonal = (top_right + bottom_left) >> 1

        if (
            (vertical_grad <= horizontal_grad)
            and (vertical_grad <= left_diagonal_grad)
            and (vertical_grad <= right_diagonal_grad)
        ):
            out_pixel = mean_vertical
        elif (horizontal_grad <= left_diagonal_grad) and (
            horizontal_grad <= right_diagonal_grad
        ):
            out_pixel = mean_horizontal
        elif left_diagonal_grad <= right_diagonal_grad:
            out_pixel = mean_left_diagonal
        else:
            out_pixel = mean_right_diagonal

        return {"out_pixel": to_unsigned(out_pixel, self.pixel_width), "out_is_dead": 1}
