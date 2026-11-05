COS=(1073741824, 759250125, 0, -759250125, -1073741824, -759250125, 0, 759250125)
WINDOW=(0, 157245850, 536870912, 916495974, 1073741824, 916495974, 536870912, 157245850)


def signed(v):
    return v-4294967296 if v>=2147483648 else v


def round_even(v,d):
    mag=-v if v<0 else v
    q=mag//d
    rem=mag-q*d
    if rem*2>d or (rem*2==d and q%2==1):
        q+=1
    return -q if v<0 else q


class AiMlAudioStftIstftOverlapAdd:
    def __init__(self):
        self.reset()

    def reset(self):
        self.prev_tail=[0]*4
        self.have_tail=0
        self.sample_index=0

    def process(self, in_start, in_bin0_re, in_bin0_im, in_bin1_re, in_bin1_im, in_bin2_re, in_bin2_im, in_bin3_re, in_bin3_im, in_bin4_re, in_bin4_im):
        re=[signed(in_bin0_re), signed(in_bin1_re), signed(in_bin2_re), signed(in_bin3_re), signed(in_bin4_re)]
        im=[signed(in_bin0_im), signed(in_bin1_im), signed(in_bin2_im), signed(in_bin3_im), signed(in_bin4_im)]
        weighted=[0]*8
        for n in range(8):
            total=re[0]*1073741824 + re[4]*COS[(4*n)%8]
            for k in range(1,4):
                c=COS[(k*n)%8]
                s=COS[(2-k*n)%8]
                total+=2*(re[k]*c-im[k]*s)
            sample=round_even(total,8589934592)
            weighted[n]=round_even(sample*WINDOW[n],1073741824)
        records=[]
        if in_start==0 and self.have_tail==1:
            for i in range(4):
                denominator=WINDOW[i]*WINDOW[i]+WINDOW[i+4]*WINDOW[i+4]
                value=round_even((self.prev_tail[i]+weighted[i])*1152921504606846976,denominator)
                clipped=0
                if value>2147483647:
                    value=2147483647
                    clipped=1
                elif value< -2147483648:
                    value= -2147483648
                    clipped=1
                records.append({"out_sample_idx":(self.sample_index+i)&4294967295,"out_sample":value&4294967295,"out_clipped":clipped})
            self.sample_index=(self.sample_index+4)&4294967295
        else:
            self.sample_index=0
        for i in range(4):
            self.prev_tail[i]=weighted[i+4]
        self.have_tail=1
        return records
