ATAN = (536870912, 316933406, 167458907, 85004756, 42667331, 21354465, 10679838, 5340245, 2670163, 1335087, 667544, 333772, 166886, 83443, 41722, 20861, 10430, 5215, 2608, 1304, 652, 326, 163, 81, 41, 20, 10, 5, 3, 1)
GAIN_INV = 652032874


def signed(value, width):
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


def magnitude(x, y):
    square = (x*x + y*y) << 32
    root = 0
    for bit in range(31, -1, -1):
        trial = root | (1 << bit)
        if trial*trial <= square:
            root = trial
    return root


def angle(x, y):
    if y == 0:
        return 0 if x >= 0 else 1 << 31
    if x == 0:
        return (1 << 30) if y > 0 else -(1 << 30)
    phase = 0
    if x < 0:
        phase = (1 << 31) if y > 0 else -(1 << 31)
        x, y = -x, -y
    x <<= 24
    y <<= 24
    for i in range(30):
        old_x = x
        if y >= 0:
            x += y >> i
            y -= old_x >> i
            phase += ATAN[i]
        else:
            x -= y >> i
            y += old_x >> i
            phase -= ATAN[i]
    return phase


def rotate(mag, phase):
    x = (mag * GAIN_INV) >> 30
    y = 0
    z = signed(phase, 32)
    if z > (1 << 30):
        x = -x
        z -= 1 << 31
    elif z < -(1 << 30):
        x = -x
        z += 1 << 31
    for i in range(30):
        old_x = x
        if z >= 0:
            x -= y >> i
            y += old_x >> i
            z -= ATAN[i]
        else:
            x += y >> i
            y -= old_x >> i
            z += ATAN[i]
    return (x + 32768) >> 16, (y + 32768) >> 16


WINDOW = (0, 624, 2399, 5057, 8192, 11327, 13985, 15760, 16384, 15760, 13985, 11327, 8192, 5057, 2399, 624)
COSINE = (16384, 15137, 11585, 6270, 0, -6270, -11585, -15137, -16384, -15137, -11585, -6270, 0, 6270, 11585, 15137)
SINE = (0, 6270, 11585, 15137, 16384, 15137, 11585, 6270, 0, -6270, -11585, -15137, -16384, -15137, -11585, -6270)

def nearest(x,d):
    q=abs(x)//d
    r=abs(x)-q*d
    if 2*r>d or (2*r==d and (q & 1)!=0):q+=1
    if x<0:q=-q
    return q

def spectrum(samples):
    re=[0]*162
    im=[0]*162
    for frame in range(17):
        for k in range(9):
            a=0
            b=0
            for j in range(16):
                index=frame*4+j-8
                if 0<=index and index<64:
                    sample=signed(samples >> (16*index),16)
                    a+=sample*WINDOW[j]*COSINE[(k*j)%16]
                    b-=sample*WINDOW[j]*SINE[(k*j)%16]
            re[frame*9+k]=nearest(a,4294967296)
            im[frame*9+k]=nearest(b,4294967296)
    return [re,im]

def stretch(samples,rate):
    spectra=spectrum(samples)
    re=spectra[0]
    im=spectra[1]
    phases=[0]*9
    for k in range(9):phases[k]=angle(re[k],im[k])
    accum=[0]*148
    norms=[0]*148
    frames=(4352+rate-1)//rate
    for frame in range(34):
        if frame<frames:
            step=frame*rate
            left=step//256
            alpha=step & 255
            rr=[0]*9
            ii=[0]*9
            for k in range(9):
                x0=re[left*9+k]
                y0=im[left*9+k]
                x1=re[(left+1)*9+k]
                y1=im[(left+1)*9+k]
                mag=((256-alpha)*magnitude(x0,y0)+alpha*magnitude(x1,y1))//256
                value=rotate(mag,phases[k])
                rr[k]=value[0]
                ii[k]=value[1]
                advance=k*1073741824
                delta=angle(x1,y1)-angle(x0,y0)-advance
                phases[k]+=advance+delta-nearest(delta,4294967296)*4294967296
            for j in range(16):
                raw=rr[0]*16384+rr[8]*COSINE[(8*j)%16]
                for k in range(1,8):raw+=2*(rr[k]*COSINE[(k*j)%16]-ii[k]*SINE[(k*j)%16])
                sample=nearest(raw,16384)
                accum[frame*4+j]+=sample*WINDOW[j]
                norms[frame*4+j]+=WINDOW[j]*WINDOW[j]
    length=nearest(16384,rate)
    output=[0]*128
    for i in range(128):
        if i<length:
            output[i]=max(-32768,min(32767,nearest(accum[i+8]*16384,norms[i+8])))
    return output

class AudioTimeStretch:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self,in_samples,in_rate):
        status=1
        length=0
        packed=0
        if 128<=in_rate and in_rate<=512:
            status=0
            rate=in_rate
            values=stretch(in_samples,rate)
            stretched_length=nearest(16384,rate)
            length=stretched_length
            for i in range(128):
                if i<length:
                    packed |= (values[i] & 65535) << (16*i)
        return {"out_status":status,"out_length":length,"out_samples":packed}
