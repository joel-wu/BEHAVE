class DmaStrided2D:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_source_address, in_destination_address, in_line_size, in_line_repeat, in_surface_repeat, in_source_line, in_destination_line, in_source_surface, in_destination_surface, in_source_type, in_destination_type):
        bad = not (1 <= in_line_repeat <= 8192 and 1 <= in_surface_repeat <= 8192)
        bad = bad or in_line_size == 0 or (in_line_size & 31) != 0
        bad = bad or in_source_line < in_line_size or in_destination_line < in_line_size
        bad = bad or in_source_surface < in_source_line * in_line_repeat
        bad = bad or in_destination_surface < in_destination_line * in_line_repeat
        return {
            "out_source_address": 0 if bad else (in_source_address),
            "out_destination_address": 0 if bad else (in_destination_address),
            "out_line": 0 if bad else ((in_line_size >> 5) - 1),
            "out_source_type": 0 if bad else (in_source_type),
            "out_destination_type": 0 if bad else (in_destination_type),
            "out_line_repeat": 0 if bad else (in_line_repeat - 1),
            "out_source_line": 0 if bad else (in_source_line),
            "out_destination_line": 0 if bad else (in_destination_line),
            "out_surface_repeat": 0 if bad else (in_surface_repeat - 1),
            "out_source_surface": 0 if bad else (in_source_surface),
            "out_destination_surface": 0 if bad else (in_destination_surface),
            "out_enable": 0 if bad else (1),
            "out_illegal": 1 if bad else 0,
        }
