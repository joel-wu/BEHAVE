def signed(x):
    x &= 0xffffffff
    return x-(1<<32) if x&(1<<31) else x

def mul_q31(a,b):
    if a==b==-(1<<31):
        return (1<<31)-1
    n=a*b+((1<<30) if (a<0)==(b<0) else 1-(1<<30))
    return n//(1<<31) if n>=0 else -((-n)//(1<<31))

def div_round(x,n):
    q=x>>n
    remainder=x&((1<<n)-1)
    threshold=(((1<<n)-1)>>1)+(1 if q<0 else 0)
    return q+(1 if remainder>threshold else 0)

def mul_pow2(x,n):
    threshold=(1<<(31-n))-1
    return (1<<31)-1 if x>threshold else -(1<<31) if x < -threshold else x<<n

def exp_neg(v):
    a=(v&((1<<24)-1))-(1<<24)
    remainder=a-v
    x=(a<<5)+(1<<28)
    x2=mul_q31(x,x)
    poly=div_round(mul_q31(div_round(mul_q31(x2,x2),2)+mul_q31(x2,x),715827883)+x2,1)
    result=1895147668+mul_q31(1895147668,x+poly)
    constants=[1672461947,1302514674,790015084,290630308,39332535,720401,242]
    for j in range(7):
        if remainder&(1<<(24+j)):
            result=mul_q31(result,constants[j])
    return (1<<31)-1 if v==0 else result

def reciprocal(v):
    half=(v+(1<<31))//2
    x=1515870810+mul_q31(half,-1010580540)
    for _ in range(3):
        x+=mul_pow2(mul_q31(x,(1<<29)-mul_q31(half,x)),2)
    return mul_pow2(x,1)

class SignalProcessingCnnSoftmaxArmSoftmaxU8:
    def __init__(self):
        self.reset()
    def reset(self):
        pass
    def process(self,in_input,in_mult,in_shift,in_diff_min):
        mult,shift,cutoff=signed(in_mult),signed(in_shift),signed(in_diff_min)
        if mult<0 or not 0<=shift<=30 or cutoff>0:
            return {"out_output":0,"out_error":1}
        xs=[(in_input>>(8*j))&255 for j in range(4)]
        maximum=max(xs)
        diffs=[x-maximum for x in xs]
        exps=[]
        for d in diffs:
            if d>=cutoff:
                scaled=d*(1<<shift)
                if scaled<-(1<<31):
                    return {"out_output":0,"out_error":1}
                exps.append(exp_neg(mul_q31(scaled,mult)))
            else:
                exps.append(0)
        total=sum(div_round(x,12) for x in exps)
        headroom=32-total.bit_length()
        scale=reciprocal((total<<headroom)-(1<<31))
        n=35-headroom
        output=0
        for j in range(4):
            y=div_round(mul_q31(scale,exps[j]),n) if diffs[j]>=cutoff else 0
            output |= max(0,min(255,y))<<(8*j)
        return {"out_output":output,"out_error":0}
