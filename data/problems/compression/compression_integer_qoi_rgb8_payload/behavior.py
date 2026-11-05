# SPDX-License-Identifier: MIT
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class QOIRGB8Payload:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_pixels):
        cache=[0]*64
        previous=4278190080
        run=0
        count=0
        payload=0
        for i in range(8):
            pixel=((in_pixels >> (24*i)) & 16777215) | 4278190080
            if pixel == previous:
                run += 1
                if i == 7:
                    payload |= (192 | (run-1)) << (8*count)
                    count += 1
                    run=0
            else:
                if run:
                    payload |= (192 | (run-1)) << (8*count)
                    count += 1
                    run=0
                r=pixel & 255
                g=(pixel >> 8) & 255
                b=(pixel >> 16) & 255
                slot=(3*r+5*g+7*b+2805) & 63
                if cache[slot] == pixel:
                    payload |= slot << (8*count)
                    count += 1
                else:
                    cache[slot]=pixel
                    dr=s8(r-(previous & 255))
                    dg=s8(g-((previous >> 8) & 255))
                    db=s8(b-((previous >> 16) & 255))
                    dgr=s8(dr-dg)
                    dgb=s8(db-dg)
                    if -2 <= dr and dr <= 1 and -2 <= dg and dg <= 1 and -2 <= db and db <= 1:
                        payload |= (64 | ((dr+2)<<4) | ((dg+2)<<2) | (db+2)) << (8*count)
                        count += 1
                    elif -32 <= dg and dg <= 31 and -8 <= dgr and dgr <= 7 and -8 <= dgb and dgb <= 7:
                        payload |= (128 | (dg+32)) << (8*count)
                        payload |= (((dgr+8)<<4) | (dgb+8)) << (8*(count+1))
                        count += 2
                    else:
                        payload |= (254 | (r<<8) | (g<<16) | (b<<24)) << (8*count)
                        count += 4
            previous=pixel
        result=payload | (count << 256)
        return {"out_values": result}
