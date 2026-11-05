from fp_lib import fp32_add, fp32_mul, fp32_neg


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


# Precomputed cos/sin LUT for theta=10000.0 (FP32 0x461C4000),
# rotary width D=16 (=> 8 complex pairs), indexed by position p in [0,15]
# and pair index m in [0,7]:
#   inv_freq[m] = 10000.0**(-m/8)
#   COS_LUT[p][m] = fp32(cos(p * inv_freq[m]))
#   SIN_LUT[p][m] = fp32(sin(p * inv_freq[m]))
#
# Values match Python math.cos / math.sin rounded to FP32 (RNE).
COS_LUT = [
    [0x3F800000, 0x3F800000, 0x3F800000, 0x3F800000, 0x3F800000, 0x3F800000, 0x3F800000, 0x3F800000],  # p=0
    [0x3F0A5140, 0x3F734E6A, 0x3F7EB898, 0x3F7FDF3C, 0x3F7FFCB9, 0x3F7FFFAC, 0x3F7FFFF8, 0x3F7FFFFF],  # p=1
    [0xBED51133, 0x3F4E7BEC, 0x3F7AE5A5, 0x3F7F7CF9, 0x3F7FF2E5, 0x3F7FFEB0, 0x3F7FFFDE, 0x3F7FFFFD],  # p=2
    [0xBF7D7026, 0x3F152F57, 0x3F7490EF, 0x3F7ED94F, 0x3F7FE283, 0x3F7FFD0D, 0x3F7FFFB5, 0x3F7FFFF8],  # p=3
    [0xBF275530, 0x3E9A2EB1, 0x3F6BCAA7, 0x3F7DF469, 0x3F7FCB94, 0x3F7FFAC2, 0x3F7FFF7A, 0x3F7FFFF3],  # p=4
    [0x3E913C2C, 0xBC2972D4, 0x3F60A940, 0x3F7CCE81, 0x3F7FAE19, 0x3F7FF7CF, 0x3F7FFF2E, 0x3F7FFFEB],  # p=5
    [0x3F75CDB8, 0xBEA43F6F, 0x3F534932, 0x3F7B67E3, 0x3F7F8A12, 0x3F7FF434, 0x3F7FFED2, 0x3F7FFFE2],  # p=6
    [0x3F40FFBD, 0xBF1974BB, 0x3F43CCB3, 0x3F79C0EA, 0x3F7F5F81, 0x3F7FEFF2, 0x3F7FFE65, 0x3F7FFFD7],  # p=7
    [0xBE14FDF6, 0xBF5191E6, 0x3F325B5F, 0x3F77DA02, 0x3F7F2E66, 0x3F7FEB08, 0x3F7FFDE7, 0x3F7FFFCA],  # p=8
    [0xBF693FD5, 0xBF74E6A2, 0x3F1F21D5, 0x3F75B3A9, 0x3F7EF6C2, 0x3F7FE576, 0x3F7FFD59, 0x3F7FFFBC],  # p=9
    [0xBF56CD64, 0xBF7FF1FB, 0x3F0A5140, 0x3F734E6A, 0x3F7EB898, 0x3F7FDF3C, 0x3F7FFCB9, 0x3F7FFFAC],  # p=10
    [0x3B910572, 0xBF719B8C, 0x3EE83DC0, 0x3F70AAE4, 0x3F7E73E8, 0x3F7FD85B, 0x3F7FFC09, 0x3F7FFF9A],  # p=11
    [0x3F5806D0, 0xBF4B4F55, 0x3EB986F5, 0x3F6DC9C3, 0x3F7E28B5, 0x3F7FD0D2, 0x3F7FFB48, 0x3F7FFF87],  # p=12
    [0x3F684E6F, 0xBF10D99D, 0x3E88F59B, 0x3F6AABC3, 0x3F7DD700, 0x3F7FC8A1, 0x3F7FFA76, 0x3F7FFF72],  # p=13
    [0x3E0C04D7, 0xBE900D0F, 0x3E2E0BDE, 0x3F6751B1, 0x3F7D7ECC, 0x3F7FBFC9, 0x3F7FF994, 0x3F7FFF5C],  # p=14
    [0xBF427AE8, 0x3CFE22F7, 0x3D90DEAA, 0x3F63BC68, 0x3F7D201A, 0x3F7FB649, 0x3F7FF8A1, 0x3F7FFF43],  # p=15
]

SIN_LUT = [
    [0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000],  # p=0
    [0x3F576AA4, 0x3E9F393E, 0x3DCC7576, 0x3D01815C, 0x3C23D657, 0x3B4F3E20, 0x3A83126D, 0x39A5CB5F],  # p=1
    [0x3F68C7B7, 0x3F17541B, 0x3E4B6FF9, 0x3D8170C8, 0x3CA3D43E, 0x3BCF3DDD, 0x3B031269, 0x3A25CB5F],  # p=2
    [0x3E1081C3, 0x3F5009C2, 0x3E974E6D, 0x3DC1FFC1, 0x3CF5B920, 0x3C1B6E11, 0x3B449B93, 0x3A78B10D],  # p=3
    [0xBF41BDCF, 0x3F741DDE, 0x3EC761D7, 0x3E012E88, 0x3D23CBDB, 0x3C4F3CCD, 0x3B831258, 0x3AA5CB5C],  # p=4
    [0xBF757C10, 0x3F7FFC7F, 0x3EF57744, 0x3E213C1E, 0x3D4CB6F5, 0x3C818581, 0x3BA3D6DE, 0x3ACF3E31],  # p=5
    [0xBE8F0F8C, 0x3F72784D, 0x3F108C69, 0x3E41206F, 0x3D759CD1, 0x3C9B6C46, 0x3BC49B59, 0x3AF8B105],  # p=6
    [0x3F283046, 0x3F4CE86F, 0x3F24EB73, 0x3E60D350, 0x3D8F3E32, 0x3CB552A6, 0x3BE55FC7, 0x3B1111EC],  # p=7
    [0x3F7D4695, 0x3F13067D, 0x3F37A4A6, 0x3E802652, 0x3DA3AA51, 0x3CCF388E, 0x3C031213, 0x3B25CB54],  # p=8
    [0x3ED30132, 0x3E951FEA, 0x3F48881D, 0x3E8FC22E, 0x3DB8123F, 0x3CE91DEF, 0x3C13743A, 0x3B3A84BB],  # p=9
    [0xBF0B44F8, 0xBCA97082, 0x3F576AA4, 0x3E9F393E, 0x3DCC7576, 0x3D01815C, 0x3C23D657, 0x3B4F3E20],  # p=10
    [0xBF7FFF5C, 0xBEA94121, 0x3F64262A, 0x3EAE878B, 0x3DE0D372, 0x3D0E736B, 0x3C34386A, 0x3B63F785],  # p=11
    [0xBF095CD8, 0xBF1B9126, 0x3F6E9A1D, 0x3EBDA92C, 0x3DF52BAC, 0x3D1B651D, 0x3C449A71, 0x3B78B0E8],  # p=12
    [0x3ED72022, 0xBF53144C, 0x3F76ABC0, 0x3ECC9A40, 0x3E04BED0, 0x3D285669, 0x3C54FC6B, 0x3B86B525],  # p=13
    [0x3F7D9872, 0xBF75A8B2, 0x3F7C466F, 0x3EDB56F5, 0x3E0EE464, 0x3D354746, 0x3C655E56, 0x3B9111D4],  # p=14
    [0x3F267944, 0xBF7FE075, 0x3F7F5BD5, 0x3EE9DB83, 0x3E19064F, 0x3D4237AD, 0x3C75C033, 0x3B9B6E83],  # p=15
]


def _zero_outputs():
    out = {}
    for i in range(16):
        out[f"out_q_pe_rot_{i}"] = 0
        out[f"out_k_pe_rot_{i}"] = 0
    return out


class AiMlTxAttnMlaDeepseekV2MlaDecoupledRope:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_position_id,
        in_rope_theta_bits,
        in_q_pe_0, in_q_pe_1, in_q_pe_2, in_q_pe_3,
        in_q_pe_4, in_q_pe_5, in_q_pe_6, in_q_pe_7,
        in_q_pe_8, in_q_pe_9, in_q_pe_10, in_q_pe_11,
        in_q_pe_12, in_q_pe_13, in_q_pe_14, in_q_pe_15,
        in_k_pe_0, in_k_pe_1, in_k_pe_2, in_k_pe_3,
        in_k_pe_4, in_k_pe_5, in_k_pe_6, in_k_pe_7,
        in_k_pe_8, in_k_pe_9, in_k_pe_10, in_k_pe_11,
        in_k_pe_12, in_k_pe_13, in_k_pe_14, in_k_pe_15,
    ):
        theta_bits = to_unsigned(in_rope_theta_bits, 32)
        pos = to_unsigned(in_position_id, 32)
        if theta_bits != 0x461C4000 or pos > 15:
            return _zero_outputs()

        q = [
            to_unsigned(in_q_pe_0, 32), to_unsigned(in_q_pe_1, 32),
            to_unsigned(in_q_pe_2, 32), to_unsigned(in_q_pe_3, 32),
            to_unsigned(in_q_pe_4, 32), to_unsigned(in_q_pe_5, 32),
            to_unsigned(in_q_pe_6, 32), to_unsigned(in_q_pe_7, 32),
            to_unsigned(in_q_pe_8, 32), to_unsigned(in_q_pe_9, 32),
            to_unsigned(in_q_pe_10, 32), to_unsigned(in_q_pe_11, 32),
            to_unsigned(in_q_pe_12, 32), to_unsigned(in_q_pe_13, 32),
            to_unsigned(in_q_pe_14, 32), to_unsigned(in_q_pe_15, 32),
        ]
        k = [
            to_unsigned(in_k_pe_0, 32), to_unsigned(in_k_pe_1, 32),
            to_unsigned(in_k_pe_2, 32), to_unsigned(in_k_pe_3, 32),
            to_unsigned(in_k_pe_4, 32), to_unsigned(in_k_pe_5, 32),
            to_unsigned(in_k_pe_6, 32), to_unsigned(in_k_pe_7, 32),
            to_unsigned(in_k_pe_8, 32), to_unsigned(in_k_pe_9, 32),
            to_unsigned(in_k_pe_10, 32), to_unsigned(in_k_pe_11, 32),
            to_unsigned(in_k_pe_12, 32), to_unsigned(in_k_pe_13, 32),
            to_unsigned(in_k_pe_14, 32), to_unsigned(in_k_pe_15, 32),
        ]

        out = {}
        for m in range(8):
            c = COS_LUT[pos][m]
            s = SIN_LUT[pos][m]
            i0 = 2 * m
            i1 = i0 + 1
            qr = q[i0]
            qi = q[i1]
            kr = k[i0]
            ki = k[i1]

            # q_rot_real = qr*c - qi*s    -> fp32_add(fp32_mul(qr,c), fp32_neg(fp32_mul(qi,s)))
            # q_rot_imag = qr*s + qi*c
            q_rot_real = fp32_add(fp32_mul(qr, c), fp32_neg(fp32_mul(qi, s)))
            q_rot_imag = fp32_add(fp32_mul(qr, s), fp32_mul(qi, c))
            k_rot_real = fp32_add(fp32_mul(kr, c), fp32_neg(fp32_mul(ki, s)))
            k_rot_imag = fp32_add(fp32_mul(kr, s), fp32_mul(ki, c))

            out[f"out_q_pe_rot_{i0}"] = to_unsigned(q_rot_real, 32)
            out[f"out_q_pe_rot_{i1}"] = to_unsigned(q_rot_imag, 32)
            out[f"out_k_pe_rot_{i0}"] = to_unsigned(k_rot_real, 32)
            out[f"out_k_pe_rot_{i1}"] = to_unsigned(k_rot_imag, 32)

        return out
