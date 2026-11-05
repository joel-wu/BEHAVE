def signed8(x):
    x &= 255
    return x-256 if x&128 else x
def clip(x):
    return max(0,min(255,x))
def sign(x):
    return 1 if x>0 else -1 if x<0 else 0

class signal_processing_video_loop_hevc_sao:
    def __init__(self):
        self.reset()
    def reset(self):
        pass
    def process(self,in_mode,in_rec,in_a,in_b,in_c,in_f,
                in_offset0,in_offset1,in_offset2,in_offset3,in_offset4,
                in_band_offsets,in_tc_q,in_tc,in_mask_q):
        mode=in_mode&7
        rec,a,b,c,f=[v&255 for v in (in_rec,in_a,in_b,in_c,in_f)]
        offsets=[signed8(v) for v in (in_offset0,in_offset1,in_offset2,in_offset3,in_offset4)]
        result,output_sign=rec,0
        if mode==0:
            output_sign=sign(rec-a)
        elif mode<=4:
            result=clip(rec+offsets[sign(rec-a)+sign(rec-b)+2])
        elif mode==5:
            result=clip(rec+signed8(in_band_offsets>>(8*(rec>>3))))
        elif mode==6:
            prediction=(f+2*a+2*rec+2*b+c+4)>>3
            tc=in_tc_q&255
            result=rec+max(-tc,min(tc,prediction-rec))
        else:
            delta=(4*(rec-a)+c-b+4)>>3
            tc=in_tc&255
            delta=max(-tc,min(tc,delta))
            result=clip(rec-delta) if in_mask_q&1 else rec
        return {"out_rec":result,"out_sign":output_sign&3}
