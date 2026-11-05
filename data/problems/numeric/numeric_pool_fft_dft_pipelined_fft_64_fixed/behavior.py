"""Framed, signed-integer 8-by-8 Cooley--Tukey transform.

An explicit numerical/transport adaptation, not a cycle model of the upstream
continuous pipeline. Source attribution and license notices: see data/SOURCES.csv.
"""

COS = (32767, 32610, 32138, 31357, 30274, 28899, 27246, 25330,
       23170, 20788, 18205, 15447, 12540, 9512, 6393, 3212, 0)


def signed(value, width):
    return (value + (1 << (width - 1))) % (1 << width) - (1 << (width - 1))


def twiddle(index):
    quadrant, offset = divmod(index % 64, 16)
    c, s = COS[offset], COS[16 - offset]
    return ((c, s), (-s, c), (-c, -s), (s, -c))[quadrant]


def dft8(values):
    result = []
    for k in range(8):
        real = imag = 0
        for n in range(8):
            c, s = twiddle(8 * n * k)
            r, i = values[n]
            real += r * c + i * s
            imag += i * c - r * s
        result.append((real >> 15, imag >> 15))
    return result


class FramedFft64Fixed:
    def __init__(self):
        self.reset()

    def reset(self):
        self.samples = []
        self.frame_shift = 0

    def process(self, in_shift, in_dr, in_di):
        if not self.samples:
            self.frame_shift = in_shift & 15
        self.samples.append((signed(in_dr, 16), signed(in_di, 16)))
        if len(self.samples) < 64:
            return None

        first = []
        ovf1 = 0
        for r in range(8):
            row = []
            for re, im in dft8(self.samples[r::8]):
                re = (re << (self.frame_shift & 3)) >> 1
                im = (im << (self.frame_shift & 3)) >> 1
                ovf1 |= int(not (-131072 <= re <= 131071 and -131072 <= im <= 131071))
                row.append((signed(re, 18), signed(im, 18)))
            first.append(row)

        rotated = []
        for r in range(8):
            row = []
            for q in range(8):
                re, im = first[r][q]
                c, s = twiddle(r * q)
                row.append((signed((re * c + im * s) >> 16, 18),
                            signed((im * c - re * s) >> 16, 18)))
            rotated.append(row)

        output = [(0, 0)] * 64
        ovf2 = 0
        for q in range(8):
            for p, (re, im) in enumerate(dft8([rotated[r][q] for r in range(8)])):
                re = (re << (self.frame_shift >> 2)) >> 1
                im = (im << (self.frame_shift >> 2)) >> 1
                ovf2 |= int(not (-262144 <= re <= 262143 and -262144 <= im <= 262143))
                output[q + 8 * p] = (re & 524287, im & 524287)
        self.samples = []
        return [dict(out_addr=k, out_dor=re, out_doi=im, out_ovf1=ovf1, out_ovf2=ovf2)
                for k, (re, im) in enumerate(output)]
