# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class NoRepeatNGram8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_tokens, in_length, in_ngram_minus1, in_scores):
        length=min(8,in_length)
        n=in_ngram_minus1+1
        banned=0
        for start in range(8):
            if start+n<=length:
                match=1
                for j in range(3):
                    if j<n-1:
                        a=(in_tokens >> (4*(start+j)))&15
                        b=(in_tokens >> (4*(length-n+1+j)))&15
                        if a!=b:
                            match=0
                if match:
                    banned |= 1 << ((in_tokens >> (4*(start+n-1)))&15)
        result=banned << 256
        for v in range(16):
            if not ((banned >> v)&1):
                result |= ((in_scores >> (16*v))&65535) << (16*v)
        return {"out_values": result}
