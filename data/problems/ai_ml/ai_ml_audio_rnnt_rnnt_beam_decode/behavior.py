S=65536
Q=1099511627776
LN2=762123384786

def s32(value):
    value &= 0xFFFFFFFF
    return value - 4294967296 if value >= 2147483648 else value

def sat(value):
    return max(-2147483648, min(2147483647, value))

def rne(numerator, denominator):
    negative = numerator < 0
    magnitude = -numerator if negative else numerator
    quotient = magnitude // denominator
    remainder = magnitude - quotient * denominator
    if 2 * remainder > denominator or (2 * remainder == denominator and quotient & 1):
        quotient += 1
    return -quotient if negative else quotient

def exp40(value):
    if value <= -32 * S:
        return 0
    # Callers bound positive arguments below 11; negative arguments may be any Q16.16 value.
    wide = value * 16777216
    exponent = wide // LN2
    residual = wide - exponent * LN2
    term = Q
    total = Q
    for degree in range(1, 13):
        term = rne(term * residual, Q * degree)
        total += term
    if exponent >= 0:
        return total << exponent
    return rne(total, 1 << (-exponent))

def softplus16(value):
    magnitude = -value if value < 0 else value
    small = exp40(-magnitude)
    z = rne(small * Q, 2 * Q + small)
    square = rne(z * z, Q)
    term = z
    total = z
    for index in range(1, 12):
        term = rne(term * square, Q)
        total += rne(term, 2 * index + 1)
    correction = rne(2 * total, 16777216)
    return sat(max(value, 0) + correction)


def logadd(a,b):
    difference=abs(a-b)
    correction=0 if difference>=1048576 else softplus16(-difference)
    return max(a,b)+correction


class AiMlAudioRnntRnntBeamDecode:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_beam_width, in_vocab_size, in_blank, in_num_hypos, in_tokens_flat, in_lengths, in_scores_flat, in_probs_flat):
        bad = in_beam_width<1 or in_beam_width>8 or in_vocab_size<1 or in_vocab_size>8
        bad = bad or in_num_hypos>8 or in_blank!=in_vocab_size-1
        count=1 if in_num_hypos==0 else min(in_num_hypos,8)
        keys=[0]*8
        lengths=[0]*8
        scores=[0]*8
        for h in range(8):
            if h<count:
                if in_num_hypos==0:
                    lengths[h]=1
                    keys[h]=in_blank
                else:
                    length=(in_lengths>>(6*h))&63
                    if length>31:
                        bad=True
                    length=min(length,31)
                    lengths[h]=length
                    keys[h]=(in_tokens_flat>>(256*h))&((1<<(8*length))-1)
                    scores[h]=s32((in_scores_flat>>(32*h))&4294967295)
        bkeys=[0]*8
        blens=[0]*8
        bscores=[0]*8
        bc=0
        for h in range(8):
            if h<count:
                found=-1
                for j in range(8):
                    if j<bc and bkeys[j]==keys[h] and blens[j]==lengths[h]:
                        found=j
                p=s32((in_probs_flat>>(32*(8*h+min(in_blank,7))))&4294967295)
                score=scores[h]+p
                if found>=0:
                    bscores[found]=logadd(bscores[found],score)
                else:
                    bkeys[bc]=keys[h]
                    blens[bc]=lengths[h]
                    bscores[bc]=score
                    bc+=1
        for i in range(8):
            for j in range(7):
                if j+1<bc and bscores[j]>bscores[j+1]:
                    bscores[j],bscores[j+1]=bscores[j+1],bscores[j]
                    bkeys[j],bkeys[j+1]=bkeys[j+1],bkeys[j]
                    blens[j],blens[j+1]=blens[j+1],blens[j]
        candidates=[0]*56
        live=[0]*56
        for h in range(8):
            for token in range(7):
                if h<count and token<in_vocab_size-1:
                    slot=h*7+token
                    candidates[slot]=scores[h]+s32((in_probs_flat>>(32*(8*h+token)))&4294967295)
                    live[slot]=1
        threshold=bscores[bc-in_beam_width] if bc>=in_beam_width and 1<=in_beam_width<=8 else 0
        ac=0
        a_tokens=0
        a_lengths=0
        a_scores=0
        for rank in range(8):
            best=-1
            bestscore=0
            for slot in range(56):
                if live[slot]==1 and (best<0 or candidates[slot]>bestscore):
                    best=slot
                    bestscore=candidates[slot]
            if best>=0:
                live[best]=0
                if rank<in_beam_width and (bc<in_beam_width or bestscore>threshold):
                    parent=best//7
                    token=best%7
                    length=lengths[parent]
                    a_tokens|=(keys[parent]|(token<<(8*length)))<<(256*ac)
                    a_lengths|=(length+1)<<(6*ac)
                    a_scores|=(bestscore&18446744073709551615)<<(64*ac)
                    ac+=1
        b_tokens=0
        b_lengths=0
        b_scores=0
        for j in range(8):
            if j<bc:
                b_tokens|=bkeys[j]<<(256*j)
                b_lengths|=blens[j]<<(6*j)
                b_scores|=(bscores[j]&18446744073709551615)<<(64*j)
        return {"out_a_count":0 if bad else ac,"out_a_tokens":0 if bad else a_tokens,
                "out_a_lengths":0 if bad else a_lengths,"out_a_scores":0 if bad else a_scores,
                "out_b_count":0 if bad else bc,"out_b_tokens":0 if bad else b_tokens,
                "out_b_lengths":0 if bad else b_lengths,"out_b_scores":0 if bad else b_scores,
                "out_error":1 if bad else 0}
