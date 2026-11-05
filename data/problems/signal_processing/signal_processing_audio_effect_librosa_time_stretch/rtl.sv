module audio_time_stretch (
    input logic clk,rst,
    input logic in_valid,output logic in_ready,
    input logic [1023:0] in_samples,
    input logic [9:0] in_rate,
    output logic out_valid,input logic out_ready,
    output logic out_status,
    output logic [7:0] out_length,
    output logic [2047:0] out_samples
);
localparam logic [479:0] WINDOW={30'h270,30'h95f,30'h13c1,30'h2000,30'h2c3f,30'h36a1,30'h3d90,30'h4000,30'h3d90,30'h36a1,30'h2c3f,30'h2000,30'h13c1,30'h95f,30'h270,30'h0};
localparam logic [511:0] COSINE={32'h3b21,32'h2d41,32'h187e,32'h0,32'hffffe782,32'hffffd2bf,32'hffffc4df,32'hffffc000,32'hffffc4df,32'hffffd2bf,32'hffffe782,32'h0,32'h187e,32'h2d41,32'h3b21,32'h4000},SINE={32'hffffe782,32'hffffd2bf,32'hffffc4df,32'hffffc000,32'hffffc4df,32'hffffd2bf,32'hffffe782,32'h0,32'h187e,32'h2d41,32'h3b21,32'h4000,32'h3b21,32'h2d41,32'h187e,32'h0};
localparam logic [959:0] ATAN={32'h1,32'h3,32'h5,32'ha,32'h14,32'h29,32'h51,32'ha3,32'h146,32'h28c,32'h518,32'ha30,32'h145f,32'h28be,32'h517d,32'ha2fa,32'h145f3,32'h28be6,32'h517cc,32'ha2f98,32'h145f2f,32'h28be53,32'h517c55,32'ha2f61e,32'h145d7e1,32'h28b0d43,32'h51111d4,32'h9fb385b,32'h12e4051e,32'h20000000};

function automatic signed [63:0] nearest(input logic signed [63:0] x,d);
    logic signed [63:0] q,r,m;
    begin m=x<0 ? -x:x;q=m/d;r=m-q*d;
        if(2*r>d || (2*r==d && q[0]))q=q+1;
        nearest=x<0 ? -q:q;
    end
endfunction
function automatic signed [63:0] angle(input logic signed [31:0] ix,iy);
    logic signed [63:0] x,y,z,old;integer n;
    begin
        x=ix;y=iy;z=0;
        if(iy==0)z=ix<0 ? 64'sd2147483648:0;
        else if(ix==0)z=iy>0 ? 64'sd1073741824:-64'sd1073741824;
        else begin
            if(ix<0)begin z=iy>0 ? 64'sd2147483648:-64'sd2147483648;x=-x;y=-y;end
            x=x<<<24;y=y<<<24;
            for(n=0;n<30;n=n+1)begin
                old=x;
                if(y>=0)begin x=x+(y>>>n);y=y-(old>>>n);z=z+64'(ATAN[n*32+:32]);end
                else begin x=x-(y>>>n);y=y+(old>>>n);z=z-64'(ATAN[n*32+:32]);end
            end
        end
        angle=z;
    end
endfunction
function automatic signed [63:0] magnitude(input logic signed [31:0] ix,iy);
    logic [63:0] square,root,trial;integer n;
    begin
        square=(64'(ix)*64'(ix)+64'(iy)*64'(iy))<<32;root=0;
        for(n=31;n>=0;n=n-1)begin trial=root|(64'd1<<n);if(trial*trial<=square)root=trial;end
        magnitude=root;
    end
endfunction
function automatic [63:0] rotate(input logic signed [63:0] mag,phase);
    logic signed [63:0] x,y,z,old;integer n;
    begin
        x=(mag*652032874)>>>30;y=0;z=64'($signed(phase[31:0]));
        if(z>64'sd1073741824)begin x=-x;z=z-64'sd2147483648;end
        else if(z < -64'sd1073741824)begin x=-x;z=z+64'sd2147483648;end
        for(n=0;n<30;n=n+1)begin
            old=x;
            if(z>=0)begin x=x-(y>>>n);y=y+(old>>>n);z=z-64'(ATAN[n*32+:32]);end
            else begin x=x+(y>>>n);y=y-(old>>>n);z=z+64'(ATAN[n*32+:32]);end
        end
        rotate={32'((y+32768)>>>16),32'((x+32768)>>>16)};
    end
endfunction
localparam IDLE=0,ANALYZE=1,INIT_PHASE=2,SYNTHESIZE=3,PACK=4,EMIT=5;
logic [2:0] state;
logic [1023:0] samples;
integer rate,frame,frames,length;
integer real_bins[0:161],imag_bins[0:161];
logic signed [63:0] phases[0:8],accum[0:147],norms[0:147];
integer new_real[0:8],new_imag[0:8],rr[0:8],ii[0:8];
logic signed [63:0] next_phases[0:8],next_accum[0:147],next_norms[0:147];
integer j,k,index,left,alpha,position,fraction;
logic signed [63:0] a,b,mag,advance,delta,raw,value;
logic [63:0] rotated;
integer stretched[0:127];
logic [2047:0] result;
always_comb begin
    index=0;left=0;alpha=0;position=0;fraction=0;a=0;b=0;mag=0;advance=0;delta=0;raw=0;value=0;rotated=0;result=0;
    for(k=0;k<9;k=k+1)begin new_real[k]=0;new_imag[k]=0;rr[k]=0;ii[k]=0;next_phases[k]=phases[k];end
    for(j=0;j<148;j=j+1)begin next_accum[j]=accum[j];next_norms[j]=norms[j];end
    for(j=0;j<128;j=j+1)stretched[j]=0;
    if(state==ANALYZE)begin
        for(k=0;k<9;k=k+1)begin
            a=0;b=0;
            for(j=0;j<16;j=j+1)begin
                index=frame*4+j-8;
                if(index>=0 && index<64)begin
                    value=64'($signed(samples[index*16+:16]));
                    a=a+value*64'(WINDOW[j*30+:30])*64'($signed(COSINE[((k*j)%16)*32+:32]));
                    b=b-value*64'(WINDOW[j*30+:30])*64'($signed(SINE[((k*j)%16)*32+:32]));
                end
            end
            new_real[k]=nearest(a,64'sd4294967296);new_imag[k]=nearest(b,64'sd4294967296);
        end
    end
    if(state==SYNTHESIZE)begin
        left=(frame*rate)>>8;alpha=(frame*rate)&255;
        for(k=0;k<9;k=k+1)begin
            mag=((256-alpha)*magnitude(real_bins[left*9+k],imag_bins[left*9+k])+alpha*magnitude(real_bins[(left+1)*9+k],imag_bins[(left+1)*9+k]))>>>8;
            rotated=rotate(mag,phases[k]);rr[k]=$signed(rotated[31:0]);ii[k]=$signed(rotated[63:32]);
            advance=64'(k)*1073741824;
            delta=angle(real_bins[(left+1)*9+k],imag_bins[(left+1)*9+k])-angle(real_bins[left*9+k],imag_bins[left*9+k])-advance;
            next_phases[k]=phases[k]+advance+delta-nearest(delta,64'sd4294967296)*64'sd4294967296;
        end
        for(j=0;j<16;j=j+1)begin
            raw=64'(rr[0])*16384+64'(rr[8])*64'($signed(COSINE[((8*j)%16)*32+:32]));
            for(k=1;k<8;k=k+1)raw=raw+2*(64'(rr[k])*64'($signed(COSINE[((k*j)%16)*32+:32]))-64'(ii[k])*64'($signed(SINE[((k*j)%16)*32+:32])));
            value=nearest(raw,16384);
            next_accum[frame*4+j]=accum[frame*4+j]+value*64'(WINDOW[j*30+:30]);
            next_norms[frame*4+j]=norms[frame*4+j]+64'(WINDOW[j*30+:30])*64'(WINDOW[j*30+:30]);
        end
    end
    if(state==PACK)begin
        for(j=0;j<128;j=j+1)if(j<length)begin
            value=nearest(accum[j+8]*16384,norms[j+8]);
            if(value>32767)value=32767;else if(value < -32768)value=-32768;
            stretched[j]=value;
        end
        for(j=0;j<128;j=j+1)result[j*16+:16]=stretched[j];
    end
end
assign in_ready=state==IDLE;
assign out_valid=state==EMIT;
always_ff @(posedge clk)begin
    if(rst)begin
        state<=IDLE;samples<=0;rate<=256;frame<=0;frames<=0;length<=0;
        out_status<=0;out_length<=0;out_samples<=0;
        for(integer n=0;n<162;n=n+1)begin real_bins[n]<=0;imag_bins[n]<=0;end
        for(integer n=0;n<9;n=n+1)phases[n]<=0;
        for(integer n=0;n<148;n=n+1)begin accum[n]<=0;norms[n]<=0;end
    end else case(state)
        IDLE:if(in_valid)begin
            samples<=in_samples;frame<=0;
            for(integer n=0;n<148;n=n+1)begin accum[n]<=0;norms[n]<=0;end
            if(in_rate<128 || in_rate>512)begin out_status<=1;state<=EMIT;end
            else begin rate<=in_rate;state<=ANALYZE;end
        end
        ANALYZE:begin
            for(integer n=0;n<9;n=n+1)begin real_bins[frame*9+n]<=new_real[n];imag_bins[frame*9+n]<=new_imag[n];end
            if(frame==16)begin state<=INIT_PHASE;frame<=0;end else frame<=frame+1;
        end
        INIT_PHASE:begin
            for(integer n=0;n<9;n=n+1)phases[n]<=angle(real_bins[n],imag_bins[n]);
            frames<=(4352+rate-1)/rate;length<=nearest(16384,rate);state<=SYNTHESIZE;
        end
        SYNTHESIZE:begin
            for(integer n=0;n<9;n=n+1)phases[n]<=next_phases[n];
            for(integer n=0;n<148;n=n+1)begin accum[n]<=next_accum[n];norms[n]<=next_norms[n];end
            if(frame==frames-1)state<=PACK;else frame<=frame+1;
        end
        PACK:begin out_samples<=result[2047:0];out_length<=length;state<=EMIT;end
        EMIT:if(out_ready)begin state<=IDLE;out_status<=0;out_length<=0;out_samples<=0;end
        default:state<=IDLE;
    endcase
end
endmodule
