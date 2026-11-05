class AxisUpscale:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, dfmt_enable, dfmt_type, dfmt_se, s_axis_data):
        bit = (s_axis_data >> 23) ^ (dfmt_enable & dfmt_type)
        extension = 255 if dfmt_enable and dfmt_se and bit else 0
        return {"m_axis_data": (extension << 24) | (bit << 23) | (s_axis_data & 0x7fffff)}
