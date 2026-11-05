# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def away(n, d):
    return -((-n+d//2)//d) if n < 0 else (n+d//2)//d

class RoiAlignQ4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_rois):
        result=0
        for r in range(2):
            x1=s8(in_rois >> (32*r))
            y1=s8(in_rois >> (32*r+8))
            w=max(16,s8(in_rois >> (32*r+16))-x1)
            h=max(16,s8(in_rois >> (32*r+24))-y1)
            for c in range(2):
                for by in range(2):
                    for bx in range(2):
                        value=0
                        for sy in range(2):
                            for sx in range(2):
                                px=8*x1+(4*bx+2*sx+1)*w
                                py=8*y1+(4*by+2*sy+1)*h
                                if -128 <= px and px <= 512 and -128 <= py and py <= 512:
                                    px=max(0,min(384,px))
                                    py=max(0,min(384,py))
                                    ix=px//128
                                    iy=py//128
                                    fx=px-128*ix
                                    fy=py-128*iy
                                    for dy in range(2):
                                        for dx in range(2):
                                            wx=fx if dx else 128-fx
                                            wy=fy if dy else 128-fy
                                            x=min(3,ix+dx)
                                            y=min(3,iy+dy)
                                            value += wx*wy*s8(in_data >> (8*(16*c+4*y+x)))
                        result |= (away(value,256) & 65535) << (16*(8*r+4*c+2*by+bx))
        return {"out_values": result}
