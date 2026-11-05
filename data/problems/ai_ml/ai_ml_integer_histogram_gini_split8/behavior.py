# SPDX-License-Identifier: BSD-3-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class HistogramGiniSplit8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_counts):
        total0=0
        total1=0
        for b in range(8):
            total0+=(in_counts >> (8*b)) & 15
            total1+=(in_counts >> (8*b+4)) & 15
        left0=0
        left1=0
        best_num=0
        best_den=1
        valid=0
        result=0
        for t in range(7):
            left0+=(in_counts >> (8*t)) & 15
            left1+=(in_counts >> (8*t+4)) & 15
            right0=total0-left0
            right1=total1-left1
            l=left0+left1
            r=right0+right1
            if l>0 and r>0:
                den=(l+r)*l*r
                num=den-(left0*left0+left1*left1)*r-(right0*right0+right1*right1)*l
                if not valid or num*best_den<best_num*den:
                    valid=1
                    best_num=num
                    best_den=den
                    result=t | 8 | (left0<<4) | (left1<<12) | (right0<<20) | (right1<<28) | (num<<36) | (den<<60)
        return {"out_values": result}
