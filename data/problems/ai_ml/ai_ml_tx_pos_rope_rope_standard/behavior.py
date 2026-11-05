from fp_lib import fp32_add, fp32_mul, fp32_neg


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


# Precomputed cos/sin LUT for theta=10000.0 (FP32 0x461C4000),
# indexed by position p in [0,15], and j in [0,3]:
#   inv[j] = 10000.0**(-j/4)
#   cos_lut[p][j] = fp32(cos(p * inv[j]))
#   sin_lut[p][j] = fp32(sin(p * inv[j]))
#
# The values match Python math.cos / math.sin rounded to FP32 (RNE).
COS_LUT = [
    [0x3F800000, 0x3F800000, 0x3F800000, 0x3F800000],  # p=0
    [0x3F0A5140, 0x3F7EB898, 0x3F7FFCB9, 0x3F7FFFF8],  # p=1
    [0xBED51133, 0x3F7AE5A5, 0x3F7FF2E5, 0x3F7FFFDE],  # p=2
    [0xBF7D7026, 0x3F7490EF, 0x3F7FE283, 0x3F7FFFB5],  # p=3
    [0xBF275530, 0x3F6BCAA7, 0x3F7FCB94, 0x3F7FFF7A],  # p=4
    [0x3E913C2C, 0x3F60A940, 0x3F7FAE19, 0x3F7FFF2E],  # p=5
    [0x3F75CDB8, 0x3F534932, 0x3F7F8A12, 0x3F7FFED2],  # p=6
    [0x3F40FFBD, 0x3F43CCB3, 0x3F7F5F81, 0x3F7FFE65],  # p=7
    [0xBE14FDF6, 0x3F325B5F, 0x3F7F2E66, 0x3F7FFDE7],  # p=8
    [0xBF693FD5, 0x3F1F21D5, 0x3F7EF6C2, 0x3F7FFD59],  # p=9
    [0xBF56CD64, 0x3F0A5140, 0x3F7EB898, 0x3F7FFCB9],  # p=10
    [0x3B910572, 0x3EE83DC0, 0x3F7E73E8, 0x3F7FFC09],  # p=11
    [0x3F5806D0, 0x3EB986F5, 0x3F7E28B5, 0x3F7FFB48],  # p=12
    [0x3F684E6F, 0x3E88F59B, 0x3F7DD700, 0x3F7FFA76],  # p=13
    [0x3E0C04D7, 0x3E2E0BDE, 0x3F7D7ECC, 0x3F7FF994],  # p=14
    [0xBF427AE8, 0x3D90DEAA, 0x3F7D201A, 0x3F7FF8A1],  # p=15
]

SIN_LUT = [
    [0x00000000, 0x00000000, 0x00000000, 0x00000000],  # p=0
    [0x3F576AA4, 0x3DCC7576, 0x3C23D657, 0x3A83126D],  # p=1
    [0x3F68C7B7, 0x3E4B6FF9, 0x3CA3D43E, 0x3B031269],  # p=2
    [0x3E1081C3, 0x3E974E6D, 0x3CF5B920, 0x3B449B93],  # p=3
    [0xBF41BDCF, 0x3EC761D7, 0x3D23CBDB, 0x3B831258],  # p=4
    [0xBF757C10, 0x3EF57744, 0x3D4CB6F5, 0x3BA3D6DE],  # p=5
    [0xBE8F0F8C, 0x3F108C69, 0x3D759CD1, 0x3BC49B59],  # p=6
    [0x3F283046, 0x3F24EB73, 0x3D8F3E32, 0x3BE55FC7],  # p=7
    [0x3F7D4695, 0x3F37A4A6, 0x3DA3AA51, 0x3C031213],  # p=8
    [0x3ED30132, 0x3F48881D, 0x3DB8123F, 0x3C13743A],  # p=9
    [0xBF0B44F8, 0x3F576AA4, 0x3DCC7576, 0x3C23D657],  # p=10
    [0xBF7FFF5C, 0x3F64262A, 0x3DE0D372, 0x3C34386A],  # p=11
    [0xBF095CD8, 0x3F6E9A1D, 0x3DF52BAC, 0x3C449A71],  # p=12
    [0x3ED72022, 0x3F76ABC0, 0x3E04BED0, 0x3C54FC6B],  # p=13
    [0x3F7D9872, 0x3F7C466F, 0x3E0EE464, 0x3C655E56],  # p=14
    [0x3F267944, 0x3F7F5BD5, 0x3E19064F, 0x3C75C033],  # p=15
]


def theta_invalid_or_pos_oob(theta_bits, position_id):
    # In-domain only when theta == 0x461C4000 and position <= 15
    if (theta_bits & 0xFFFFFFFF) != 0x461C4000:
        return True
    if (position_id & 0xFFFFFFFF) > 15:
        return True
    return False


class AiMlTxPosRopeRopeStandard:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_position_id, in_rope_theta, in_q, in_k):
        theta_bits = to_unsigned(in_rope_theta, 32)
        pos = to_unsigned(in_position_id, 32)
        if theta_invalid_or_pos_oob(theta_bits, pos):
            return {"out_q_rot": 0, "out_k_rot": 0}

        q = [(in_q >> (32 * i)) & 0xFFFFFFFF for i in range(8)]
        k = [(in_k >> (32 * i)) & 0xFFFFFFFF for i in range(8)]

        cos_v = [0] * 8
        sin_v = [0] * 8
        for j in range(4):
            cos_v[j] = COS_LUT[pos][j]
            sin_v[j] = SIN_LUT[pos][j]
            cos_v[4 + j] = COS_LUT[pos][j]
            sin_v[4 + j] = SIN_LUT[pos][j]

        # rotate_half(q) = [-q4, -q5, -q6, -q7, q0, q1, q2, q3]
        qrh = [
            fp32_neg(q[4]), fp32_neg(q[5]), fp32_neg(q[6]), fp32_neg(q[7]),
            q[0], q[1], q[2], q[3],
        ]
        krh = [
            fp32_neg(k[4]), fp32_neg(k[5]), fp32_neg(k[6]), fp32_neg(k[7]),
            k[0], k[1], k[2], k[3],
        ]

        q_rot = [0] * 8
        k_rot = [0] * 8
        for i in range(8):
            q_rot[i] = fp32_add(fp32_mul(q[i], cos_v[i]), fp32_mul(qrh[i], sin_v[i]))
            k_rot[i] = fp32_add(fp32_mul(k[i], cos_v[i]), fp32_mul(krh[i], sin_v[i]))

        out_q_rot = 0
        out_k_rot = 0
        for i in range(8):
            out_q_rot |= to_unsigned(q_rot[i], 32) << (32 * i)
            out_k_rot |= to_unsigned(k_rot[i], 32) << (32 * i)

        return {
            "out_q_rot": to_unsigned(out_q_rot, 256),
            "out_k_rot": to_unsigned(out_k_rot, 256),
        }
