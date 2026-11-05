# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def away(n, d):
    return -((-n+d//2)//d) if n < 0 else (n+d//2)//d

class GridSampleQ4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data, in_grid, in_align_corners, in_border):
        result=0
        for c in range(2):
            for k in range(4):
                gx=s8(in_grid >> (16*k))
                gy=s8(in_grid >> (16*k+8))
                px=2*gx+32 if in_align_corners else 3*gx+32
                py=2*gy+32 if in_align_corners else 3*gy+32
                ix=px//32
                iy=py//32
                fx=px-32*ix
                fy=py-32*iy
                value=0
                for dy in range(2):
                    for dx in range(2):
                        x=ix+dx
                        y=iy+dy
                        wx=fx if dx else 32-fx
                        wy=fy if dy else 32-fy
                        if in_border:
                            x=max(0,min(2,x))
                            y=max(0,min(2,y))
                        if 0 <= x and x < 3 and 0 <= y and y < 3:
                            value += wx*wy*s8(in_data >> (8*(9*c+3*y+x)))
                result |= (away(value,4) & 65535) << (16*(4*c+k))
        return {"out_values": result}
