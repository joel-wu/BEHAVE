def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class SignalProcessingIspBayerSharpen:
    def __init__(self):
        self.reset()

    def reset(self):
        self.frame = [[0] * 8 for _ in range(8)]
        self.line_width = [0] * 8
        self.line_index = 0
        self.column_index = 0

    def process(
        self,
        in_luma,
        in_frame_start,
        in_line_start,
        in_line_end,
        in_frame_end,
        in_strength,
        in_kernel_0_0,
        in_kernel_0_1,
        in_kernel_0_2,
        in_kernel_0_3,
        in_kernel_0_4,
        in_kernel_0_5,
        in_kernel_0_6,
        in_kernel_0_7,
        in_kernel_0_8,
        in_kernel_1_0,
        in_kernel_1_1,
        in_kernel_1_2,
        in_kernel_1_3,
        in_kernel_1_4,
        in_kernel_1_5,
        in_kernel_1_6,
        in_kernel_1_7,
        in_kernel_1_8,
        in_kernel_2_0,
        in_kernel_2_1,
        in_kernel_2_2,
        in_kernel_2_3,
        in_kernel_2_4,
        in_kernel_2_5,
        in_kernel_2_6,
        in_kernel_2_7,
        in_kernel_2_8,
        in_kernel_3_0,
        in_kernel_3_1,
        in_kernel_3_2,
        in_kernel_3_3,
        in_kernel_3_4,
        in_kernel_3_5,
        in_kernel_3_6,
        in_kernel_3_7,
        in_kernel_3_8,
        in_kernel_4_0,
        in_kernel_4_1,
        in_kernel_4_2,
        in_kernel_4_3,
        in_kernel_4_4,
        in_kernel_4_5,
        in_kernel_4_6,
        in_kernel_4_7,
        in_kernel_4_8,
        in_kernel_5_0,
        in_kernel_5_1,
        in_kernel_5_2,
        in_kernel_5_3,
        in_kernel_5_4,
        in_kernel_5_5,
        in_kernel_5_6,
        in_kernel_5_7,
        in_kernel_5_8,
        in_kernel_6_0,
        in_kernel_6_1,
        in_kernel_6_2,
        in_kernel_6_3,
        in_kernel_6_4,
        in_kernel_6_5,
        in_kernel_6_6,
        in_kernel_6_7,
        in_kernel_6_8,
        in_kernel_7_0,
        in_kernel_7_1,
        in_kernel_7_2,
        in_kernel_7_3,
        in_kernel_7_4,
        in_kernel_7_5,
        in_kernel_7_6,
        in_kernel_7_7,
        in_kernel_7_8,
        in_kernel_8_0,
        in_kernel_8_1,
        in_kernel_8_2,
        in_kernel_8_3,
        in_kernel_8_4,
        in_kernel_8_5,
        in_kernel_8_6,
        in_kernel_8_7,
        in_kernel_8_8,
    ):
        if in_frame_start:
            self.frame = [[0] * 8 for _ in range(8)]
            self.line_width = [0] * 8
            self.line_index = 0
            self.column_index = 0

        if in_line_start:
            self.column_index = 0

        y = self.line_index
        x = self.column_index
        if y < 8 and x < 8:
            self.frame[y][x] = in_luma & 0xFF
        self.column_index = self.column_index + 1

        if in_line_end:
            if y < 8:
                self.line_width[y] = self.column_index
            self.line_index = self.line_index + 1

        if not in_frame_end:
            return None

        kernel = [
            [in_kernel_0_0, in_kernel_0_1, in_kernel_0_2, in_kernel_0_3, in_kernel_0_4, in_kernel_0_5, in_kernel_0_6, in_kernel_0_7, in_kernel_0_8],
            [in_kernel_1_0, in_kernel_1_1, in_kernel_1_2, in_kernel_1_3, in_kernel_1_4, in_kernel_1_5, in_kernel_1_6, in_kernel_1_7, in_kernel_1_8],
            [in_kernel_2_0, in_kernel_2_1, in_kernel_2_2, in_kernel_2_3, in_kernel_2_4, in_kernel_2_5, in_kernel_2_6, in_kernel_2_7, in_kernel_2_8],
            [in_kernel_3_0, in_kernel_3_1, in_kernel_3_2, in_kernel_3_3, in_kernel_3_4, in_kernel_3_5, in_kernel_3_6, in_kernel_3_7, in_kernel_3_8],
            [in_kernel_4_0, in_kernel_4_1, in_kernel_4_2, in_kernel_4_3, in_kernel_4_4, in_kernel_4_5, in_kernel_4_6, in_kernel_4_7, in_kernel_4_8],
            [in_kernel_5_0, in_kernel_5_1, in_kernel_5_2, in_kernel_5_3, in_kernel_5_4, in_kernel_5_5, in_kernel_5_6, in_kernel_5_7, in_kernel_5_8],
            [in_kernel_6_0, in_kernel_6_1, in_kernel_6_2, in_kernel_6_3, in_kernel_6_4, in_kernel_6_5, in_kernel_6_6, in_kernel_6_7, in_kernel_6_8],
            [in_kernel_7_0, in_kernel_7_1, in_kernel_7_2, in_kernel_7_3, in_kernel_7_4, in_kernel_7_5, in_kernel_7_6, in_kernel_7_7, in_kernel_7_8],
            [in_kernel_8_0, in_kernel_8_1, in_kernel_8_2, in_kernel_8_3, in_kernel_8_4, in_kernel_8_5, in_kernel_8_6, in_kernel_8_7, in_kernel_8_8],
        ]

        height = self.line_index
        outputs = []
        for oy in range(height):
            width = self.line_width[oy]
            for ox in range(width):
                correlation = 0
                for dy in range(9):
                    sy = oy + dy - 4
                    for dx in range(9):
                        sx = ox + dx - 4
                        px = 0
                        if sy >= 0 and sy < height and sx >= 0 and sx < self.line_width[sy]:
                            px = self.frame[sy][sx]
                        correlation = correlation + px * kernel[dy][dx]
                smoothened = correlation >> 20
                center = self.frame[oy][ox]
                edge = center - smoothened
                sharpen = in_strength * edge
                sharpened = sharpen >> 10
                out_y = center + sharpened
                if out_y < 0:
                    out_luma = 0
                elif out_y > 255:
                    out_luma = 255
                else:
                    out_luma = out_y
                outputs.append(
                    {
                        "out_luma": to_unsigned(out_luma, 8),
                        "out_frame_start": to_unsigned(1 if oy == 0 and ox == 0 else 0, 1),
                        "out_line_start": to_unsigned(1 if ox == 0 else 0, 1),
                        "out_line_end": to_unsigned(1 if ox == width - 1 else 0, 1),
                        "out_frame_end": to_unsigned(1 if oy == height - 1 and ox == width - 1 else 0, 1),
                    }
                )

        self.frame = [[0] * 8 for _ in range(8)]
        self.line_width = [0] * 8
        self.line_index = 0
        self.column_index = 0
        return outputs
