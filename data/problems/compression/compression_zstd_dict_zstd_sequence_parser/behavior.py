def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class CompressionZstdDictZstdSequenceParser:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_byte0, in_byte1, in_byte2, in_byte3, in_byte4, in_byte5, in_byte6, in_byte7, in_size):
        b0 = in_byte0 & 0xFF
        b1 = in_byte1 & 0xFF
        b2 = in_byte2 & 0xFF
        b3 = in_byte3 & 0xFF

        if in_size < 1:
            return {
                "out_nbSeq": 0,
                "out_LLtype": 0,
                "out_OFtype": 0,
                "out_MLtype": 0,
                "out_header_size": 0,
                "out_has_error": 1,
                "out_error_code": 1,
            }

        x = b0
        if x <= 0x7F:
            nbSeq = x
            ip = 1
        elif x == 0xFF:
            if in_size < 3:
                return {
                    "out_nbSeq": 0,
                    "out_LLtype": 0,
                    "out_OFtype": 0,
                    "out_MLtype": 0,
                    "out_header_size": 0,
                    "out_has_error": 1,
                    "out_error_code": 1,
                }
            nbSeq = b1 + (b2 << 8) + 0x7F00
            ip = 3
        else:
            if in_size < 2:
                return {
                    "out_nbSeq": 0,
                    "out_LLtype": 0,
                    "out_OFtype": 0,
                    "out_MLtype": 0,
                    "out_header_size": 0,
                    "out_has_error": 1,
                    "out_error_code": 1,
                }
            nbSeq = ((x - 0x80) << 8) + b1
            ip = 2

        if nbSeq == 0:
            if ip != in_size:
                return {
                    "out_nbSeq": 0,
                    "out_LLtype": 0,
                    "out_OFtype": 0,
                    "out_MLtype": 0,
                    "out_header_size": 0,
                    "out_has_error": 1,
                    "out_error_code": 2,
                }
            return {
                "out_nbSeq": 0,
                "out_LLtype": 0,
                "out_OFtype": 0,
                "out_MLtype": 0,
                "out_header_size": to_unsigned(ip, 4),
                "out_has_error": 0,
                "out_error_code": 0,
            }

        if in_size < ip + 1:
            return {
                "out_nbSeq": 0,
                "out_LLtype": 0,
                "out_OFtype": 0,
                "out_MLtype": 0,
                "out_header_size": 0,
                "out_has_error": 1,
                "out_error_code": 1,
            }

        m = b1 if ip == 1 else (b2 if ip == 2 else b3)
        if (m & 0x03) != 0:
            return {
                "out_nbSeq": 0,
                "out_LLtype": 0,
                "out_OFtype": 0,
                "out_MLtype": 0,
                "out_header_size": 0,
                "out_has_error": 1,
                "out_error_code": 2,
            }

        return {
            "out_nbSeq": to_unsigned(nbSeq, 32),
            "out_LLtype": to_unsigned((m >> 6) & 0x3, 2),
            "out_OFtype": to_unsigned((m >> 4) & 0x3, 2),
            "out_MLtype": to_unsigned((m >> 2) & 0x3, 2),
            "out_header_size": to_unsigned(ip + 1, 4),
            "out_has_error": 0,
            "out_error_code": 0,
        }