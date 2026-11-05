def signed16(value):
    return (value & 32767) - (value & 32768)


def reflect(index, size):
    if index < 0:
        return -index-1
    if index >= size:
        return 2*size-index-1
    return index


class Fir2dSameReflectS16:
    def __init__(self):
        self.image = [0]*256

    def reset(self):
        pass

    def process(self, in_width_m1, in_height_m1, in_x, in_y,
                in_h00, in_h01, in_h02, in_h10, in_h11, in_h12,
                in_h20, in_h21, in_h22):
        width, height = in_width_m1+1, in_height_m1+1
        if in_x >= width or in_y >= height:
            return {"out_y": 0}
        kernel = [in_h00,in_h01,in_h02,in_h10,in_h11,in_h12,in_h20,in_h21,in_h22]
        total = 0
        for j in range(3):
            y = reflect(in_y+1-j, height)
            for k in range(3):
                x = reflect(in_x+1-k, width)
                total += signed16(self.image[16*y+x])*signed16(kernel[3*j+k])
        return {"out_y": total & 0xffffffff}
