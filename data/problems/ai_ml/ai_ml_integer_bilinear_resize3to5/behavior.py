# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class BilinearResize3To5:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data):
        result = 0
        for h in range(5):
            for w in range(5):
                i = h // 2
                j = w // 2
                i1 = min(i+1,2)
                j1 = min(j+1,2)
                a = h % 2
                b = w % 2
                value = (2-a)*(2-b)*((in_data >> (8*(3*i+j))) & 255)
                value += (2-a)*b*((in_data >> (8*(3*i+j1))) & 255)
                value += a*(2-b)*((in_data >> (8*(3*i1+j))) & 255)
                value += a*b*((in_data >> (8*(3*i1+j1))) & 255)
                result |= ((value+2)//4) << (8*(5*h+w))
        return {"out_values": result}
