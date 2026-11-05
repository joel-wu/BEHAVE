from fp_lib import fp32_add,fp32_mul,fp32_div,fp32_sqrt,fp32_cmp


def clean(v):
    return v & 0xFFFFFFFF


def add(a,b):
    return fp32_add(a,b)


def neg(a):
    return a ^ 0x80000000


def sub(a,b):
    return fp32_add(a,neg(b))


def mul(a,b):
    return fp32_mul(a,b)


def div(a,b):
    return fp32_div(a,b)


def root(a):
    return fp32_sqrt(a)


def less(a,b):
    return fp32_cmp(a,b)==-1


class ControlMotionTrapezoidalMotionProfile:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_xf, in_xi, in_vi, in_vmax, in_amax, in_dmax, in_t):
        in_xf=clean(in_xf)
        in_xi=clean(in_xi)
        in_vi=clean(in_vi)
        in_vmax=clean(in_vmax)
        in_amax=clean(in_amax)
        in_dmax=clean(in_dmax)
        in_t=clean(in_t)
        xf,xi,vi,vmax,amax,dmax,t = in_xf,in_xi,in_vi,in_vmax,in_amax,in_dmax,in_t
        dx = sub(xf,xi)
        stop = div(mul(vi,vi),mul(0x40000000,dmax))
        dstop = (stop & 0x7FFFFFFF) | (vi & 0x80000000)
        direction = sub(dx,dstop) >> 31
        ar = amax ^ (direction << 31)
        dr = dmax ^ ((1-direction) << 31)
        vr = vmax ^ (direction << 31)
        if less(vr ^ (direction<<31),vi ^ (direction<<31)):
            ar = neg(ar)
        ta = div(sub(vr,vi),ar)
        td = div(neg(vr),dr)
        minimum = add(mul(mul(0x3F000000,ta),add(vr,vi)),mul(mul(0x3F000000,td),vr))
        rad = 0
        if less(dx ^ (direction<<31),minimum ^ (direction<<31)):
            numerator = add(mul(dr,mul(vi,vi)),mul(mul(mul(0x40000000,ar),dr),dx))
            rad = div(numerator,sub(dr,ar))
            rad = 0 if less(rad,0) else rad
            vr = root(rad) ^ (direction<<31)
            ta = div(sub(vr,vi),ar)
            td = div(neg(vr),dr)
            ta = 0 if less(ta,0) else ta
            td = 0 if less(td,0) else td
            tv = 0
        else:
            tv = div(sub(dx,minimum),vr)
        ta_tv = add(ta,tv)
        tf = add(ta_tv,td)
        end_accel = add(add(xi,mul(vi,ta)),mul(mul(0x3F000000,ar),mul(ta,ta)))
        if less(t,0):
            y,yd,ydd = xi,vi,0
        elif less(t,ta):
            y = add(add(xi,mul(vi,t)),mul(mul(0x3F000000,ar),mul(t,t)))
            yd,ydd = add(vi,mul(ar,t)),ar
        elif less(t,ta_tv):
            y,yd,ydd = add(end_accel,mul(vr,sub(t,ta))),vr,0
        elif less(t,tf):
            elapsed = sub(t,tf)
            y = add(xf,mul(mul(0x3F000000,dr),mul(elapsed,elapsed)))
            yd,ydd = mul(dr,elapsed),dr
        else:
            y,yd,ydd = xf,0,0
        bad = less(vmax,0) or less(amax,0) or less(dmax,0)
        bad = bad or (vmax & 0x7FFFFFFF)==0 or (amax & 0x7FFFFFFF)==0 or (dmax & 0x7FFFFFFF)==0
        for value in (xf,xi,vi,vmax,amax,dmax,t,dx,stop,dstop,ar,dr,vr,minimum,rad,ta,tv,td,tf,end_accel,y,yd,ydd):
            if ((value>>23)&255)==255:
                bad = True
        return {"out_y":0 if bad else y,"out_yd":0 if bad else yd,"out_ydd":0 if bad else ydd,
                "out_ta":0 if bad else ta,"out_tv":0 if bad else tv,"out_td":0 if bad else td,
                "out_tf":0 if bad else tf,"out_error":1 if bad else 0}
