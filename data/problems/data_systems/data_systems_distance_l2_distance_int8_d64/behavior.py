def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class L2DistanceUint8D64:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_a0, in_a1, in_a2, in_a3, in_a4, in_a5, in_a6, in_a7,
        in_a8, in_a9, in_a10, in_a11, in_a12, in_a13, in_a14, in_a15,
        in_a16, in_a17, in_a18, in_a19, in_a20, in_a21, in_a22, in_a23,
        in_a24, in_a25, in_a26, in_a27, in_a28, in_a29, in_a30, in_a31,
        in_a32, in_a33, in_a34, in_a35, in_a36, in_a37, in_a38, in_a39,
        in_a40, in_a41, in_a42, in_a43, in_a44, in_a45, in_a46, in_a47,
        in_a48, in_a49, in_a50, in_a51, in_a52, in_a53, in_a54, in_a55,
        in_a56, in_a57, in_a58, in_a59, in_a60, in_a61, in_a62, in_a63,
        in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7,
        in_b8, in_b9, in_b10, in_b11, in_b12, in_b13, in_b14, in_b15,
        in_b16, in_b17, in_b18, in_b19, in_b20, in_b21, in_b22, in_b23,
        in_b24, in_b25, in_b26, in_b27, in_b28, in_b29, in_b30, in_b31,
        in_b32, in_b33, in_b34, in_b35, in_b36, in_b37, in_b38, in_b39,
        in_b40, in_b41, in_b42, in_b43, in_b44, in_b45, in_b46, in_b47,
        in_b48, in_b49, in_b50, in_b51, in_b52, in_b53, in_b54, in_b55,
        in_b56, in_b57, in_b58, in_b59, in_b60, in_b61, in_b62, in_b63
    ):
        a = [
            in_a0, in_a1, in_a2, in_a3, in_a4, in_a5, in_a6, in_a7,
            in_a8, in_a9, in_a10, in_a11, in_a12, in_a13, in_a14, in_a15,
            in_a16, in_a17, in_a18, in_a19, in_a20, in_a21, in_a22, in_a23,
            in_a24, in_a25, in_a26, in_a27, in_a28, in_a29, in_a30, in_a31,
            in_a32, in_a33, in_a34, in_a35, in_a36, in_a37, in_a38, in_a39,
            in_a40, in_a41, in_a42, in_a43, in_a44, in_a45, in_a46, in_a47,
            in_a48, in_a49, in_a50, in_a51, in_a52, in_a53, in_a54, in_a55,
            in_a56, in_a57, in_a58, in_a59, in_a60, in_a61, in_a62, in_a63,
        ]
        b = [
            in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7,
            in_b8, in_b9, in_b10, in_b11, in_b12, in_b13, in_b14, in_b15,
            in_b16, in_b17, in_b18, in_b19, in_b20, in_b21, in_b22, in_b23,
            in_b24, in_b25, in_b26, in_b27, in_b28, in_b29, in_b30, in_b31,
            in_b32, in_b33, in_b34, in_b35, in_b36, in_b37, in_b38, in_b39,
            in_b40, in_b41, in_b42, in_b43, in_b44, in_b45, in_b46, in_b47,
            in_b48, in_b49, in_b50, in_b51, in_b52, in_b53, in_b54, in_b55,
            in_b56, in_b57, in_b58, in_b59, in_b60, in_b61, in_b62, in_b63,
        ]
        distance = 0
        for i in range(64):
            d = (a[i] & 0xFF) - (b[i] & 0xFF)
            distance += d * d
        return {"out_distance": to_unsigned(distance, 22)}
