# SPDX-License-Identifier: MIT
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

def even_ratio(n,d):
    a=-n if n<0 else n
    q=a//d
    r=a-q*d
    if 2*r>d or (2*r==d and q%2!=0):
        q+=1
    return -q if n<0 else q

class Sparsemax8Q8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data):
        z=[0]*8
        for i in range(8):
            z[i]=s16(in_data >> (16*i))
        for i in range(8):
            for j in range(7):
                if z[j]<z[j+1]:
                    temp=z[j]
                    z[j]=z[j+1]
                    z[j+1]=temp
        running=0
        count=1
        total=z[0]
        for i in range(8):
            running+=z[i]
            if 256+(i+1)*z[i]>running:
                count=i+1
                total=running
        result=0
        for i in range(8):
            numerator=count*s16(in_data >> (16*i))-total+256
            if numerator>0:
                result |= even_ratio(256*numerator,count) << (17*i)
                result |= 1 << (136+i)
        return {"out_values": result}
