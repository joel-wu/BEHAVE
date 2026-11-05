# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def rol32(x,n):
    return ((x<<n)|(x>>(32-n)))&4294967295

def chacha_block(key,nonce,counter):
    initial=[1634760805,857760878,2036477234,1797285236,0,0,0,0,0,0,0,0,0,0,0,0]
    work=[0]*16
    for i in range(8):
        initial[4+i]=(key>>(32*i))&4294967295
    initial[12]=counter
    for i in range(3):
        initial[13+i]=(nonce>>(32*i))&4294967295
    for i in range(16):
        work[i]=initial[i]
    for r in range(10):
        for q in range(8):
            a=q%4
            b=4+a if q<4 else 4+(a+1)%4
            c=8+a if q<4 else 8+(a+2)%4
            d=12+a if q<4 else 12+(a+3)%4
            work[a]=(work[a]+work[b])&4294967295
            work[d]=rol32(work[d]^work[a],16)
            work[c]=(work[c]+work[d])&4294967295
            work[b]=rol32(work[b]^work[c],12)
            work[a]=(work[a]+work[b])&4294967295
            work[d]=rol32(work[d]^work[a],8)
            work[c]=(work[c]+work[d])&4294967295
            work[b]=rol32(work[b]^work[c],7)
    result=0
    for i in range(16):
        result |= ((work[i]+initial[i])&4294967295) << (32*i)
    return result

class ChaChaPolyShort15:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_key, in_nonce, in_aad, in_aad_length, in_message, in_length):
        key_stream=chacha_block(in_key,in_nonce,0)
        stream=chacha_block(in_key,in_nonce,1)
        ciphertext=(in_message^stream)&((1<<(8*in_length))-1)
        mac=0
        count=0
        if in_aad_length>0:
            mac=in_aad&((1<<(8*in_aad_length))-1)
            count+=1
        if in_length>0:
            mac |= ciphertext << (128*count)
            count+=1
        mac |= (in_aad_length | (in_length<<64)) << (128*count)
        count+=1
        r=key_stream&0x0ffffffc0ffffffc0ffffffc0fffffff
        s=(key_stream>>128)&340282366920938463463374607431768211455
        h=0
        for block in range(3):
            if block<count:
                word=(mac>>(128*block))&340282366920938463463374607431768211455
                h=((h+word+340282366920938463463374607431768211456)*r)%1361129467683753853853498429727072845819
        tag=(h+s)&340282366920938463463374607431768211455
        result=ciphertext | (tag<<120)
        return {"out_values": result}
