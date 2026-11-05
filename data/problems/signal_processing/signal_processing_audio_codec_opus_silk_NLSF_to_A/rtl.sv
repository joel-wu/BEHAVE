// Derived from Xiph Opus v1.5.2 SILK; see SOURCE_LICENSE.
module silk_nlsf_to_a (
    input logic clk, rst,
    input logic in_valid, output logic in_ready,
    input logic [4:0] in_d,
    input logic [14:0] in_NLSF_0,
    input logic [14:0] in_NLSF_1,
    input logic [14:0] in_NLSF_2,
    input logic [14:0] in_NLSF_3,
    input logic [14:0] in_NLSF_4,
    input logic [14:0] in_NLSF_5,
    input logic [14:0] in_NLSF_6,
    input logic [14:0] in_NLSF_7,
    input logic [14:0] in_NLSF_8,
    input logic [14:0] in_NLSF_9,
    input logic [14:0] in_NLSF_10,
    input logic [14:0] in_NLSF_11,
    input logic [14:0] in_NLSF_12,
    input logic [14:0] in_NLSF_13,
    input logic [14:0] in_NLSF_14,
    input logic [14:0] in_NLSF_15,
    output logic out_valid, input logic out_ready,
    output logic out_status,
    output logic [15:0] out_a_Q12_0,
    output logic [15:0] out_a_Q12_1,
    output logic [15:0] out_a_Q12_2,
    output logic [15:0] out_a_Q12_3,
    output logic [15:0] out_a_Q12_4,
    output logic [15:0] out_a_Q12_5,
    output logic [15:0] out_a_Q12_6,
    output logic [15:0] out_a_Q12_7,
    output logic [15:0] out_a_Q12_8,
    output logic [15:0] out_a_Q12_9,
    output logic [15:0] out_a_Q12_10,
    output logic [15:0] out_a_Q12_11,
    output logic [15:0] out_a_Q12_12,
    output logic [15:0] out_a_Q12_13,
    output logic [15:0] out_a_Q12_14,
    output logic [15:0] out_a_Q12_15
);
function automatic signed [63:0] rr(input logic signed [63:0] x,input integer shift);
    rr=(x+(64'sd1<<(shift-1)))>>>shift;
endfunction
function automatic signed [31:0] sat32(input logic signed [63:0] x);
    if(x>2147483647)sat32=2147483647;
    else if(x < -64'sd2147483648)sat32=-32'sd2147483648;
    else sat32=x[31:0];
endfunction
function automatic [511:0] bw(input logic [511:0] av,input integer d,input integer chirp_in);
    integer j,chirp,delta;logic signed [63:0] v;logic signed [31:0] product;
    begin
        bw=0;chirp=chirp_in;delta=chirp-65536;
        for(j=0;j<16;j=j+1)if(j<d)begin
            v=64'($signed(av[j*32+:32]))*64'(chirp);
            bw[j*32+:32]=v>>>16;
            product=chirp*delta;
            chirp=chirp+rr(64'(product),16);
        end
    end
endfunction
localparam logic [4127:0] COS = {
-32'sd8192, -32'sd8190, -32'sd8182, -32'sd8170, -32'sd8152, -32'sd8130, -32'sd8104, -32'sd8072, -32'sd8034, -32'sd7994, -32'sd7946, -32'sd7896, -32'sd7840, -32'sd7778, -32'sd7714, -32'sd7644, -32'sd7568, -32'sd7490, -32'sd7406, -32'sd7318, -32'sd7226, -32'sd7128, -32'sd7026, -32'sd6922, -32'sd6812, -32'sd6698, -32'sd6580, -32'sd6458, -32'sd6332, -32'sd6204, -32'sd6070, -32'sd5934, -32'sd5792, -32'sd5648, -32'sd5502, -32'sd5352, -32'sd5198, -32'sd5040, -32'sd4880, -32'sd4718, -32'sd4552, -32'sd4382, -32'sd4212, -32'sd4038, -32'sd3862, -32'sd3684, -32'sd3502, -32'sd3320, -32'sd3136, -32'sd2948, -32'sd2760, -32'sd2570, -32'sd2378, -32'sd2186, -32'sd1990, -32'sd1794, -32'sd1598, -32'sd1400, -32'sd1202, -32'sd1002, -32'sd802, -32'sd602, -32'sd402, -32'sd202, 32'sd0, 32'sd202, 32'sd402, 32'sd602, 32'sd802, 32'sd1002, 32'sd1202, 32'sd1400, 32'sd1598, 32'sd1794, 32'sd1990, 32'sd2186, 32'sd2378, 32'sd2570, 32'sd2760, 32'sd2948, 32'sd3136, 32'sd3320, 32'sd3502, 32'sd3684, 32'sd3862, 32'sd4038, 32'sd4212, 32'sd4382, 32'sd4552, 32'sd4718, 32'sd4880, 32'sd5040, 32'sd5198, 32'sd5352, 32'sd5502, 32'sd5648, 32'sd5792, 32'sd5934, 32'sd6070, 32'sd6204, 32'sd6332, 32'sd6458, 32'sd6580, 32'sd6698, 32'sd6812, 32'sd6922, 32'sd7026, 32'sd7128, 32'sd7226, 32'sd7318, 32'sd7406, 32'sd7490, 32'sd7568, 32'sd7644, 32'sd7714, 32'sd7778, 32'sd7840, 32'sd7896, 32'sd7946, 32'sd7994, 32'sd8034, 32'sd8072, 32'sd8104, 32'sd8130, 32'sd8152, 32'sd8170, 32'sd8182, 32'sd8190, 32'sd8192
};
localparam logic [63:0] O16=64'h1_e_9_6_5_a_d_2_3_c_b_4_7_8_f_0;
localparam logic [39:0] O10=40'h7_2_1_8_5_4_3_6_9_0;
wire [14:0] nlsf[0:15];
assign nlsf[0]=in_NLSF_0;
assign nlsf[1]=in_NLSF_1;
assign nlsf[2]=in_NLSF_2;
assign nlsf[3]=in_NLSF_3;
assign nlsf[4]=in_NLSF_4;
assign nlsf[5]=in_NLSF_5;
assign nlsf[6]=in_NLSF_6;
assign nlsf[7]=in_NLSF_7;
assign nlsf[8]=in_NLSF_8;
assign nlsf[9]=in_NLSF_9;
assign nlsf[10]=in_NLSF_10;
assign nlsf[11]=in_NLSF_11;
assign nlsf[12]=in_NLSF_12;
assign nlsf[13]=in_NLSF_13;
assign nlsf[14]=in_NLSF_14;
assign nlsf[15]=in_NLSF_15;

integer c[0:15],p[0:8],qpoly[0:8];
logic [511:0] initial_a;
integer i,j,k,n,f,index,ps,qs;
logic signed [63:0] v;
always_comb begin
    initial_a=0;f=0;index=0;ps=0;qs=0;v=0;n=0;
    for(i=0;i<16;i=i+1)c[i]=0;
    for(i=0;i<9;i=i+1)begin p[i]=0;qpoly[i]=0;end
    if(in_d==10 || in_d==16)begin
        for(i=0;i<16;i=i+1)if(i<in_d)begin
            j=nlsf[i]>>8;
            index=(in_d==10) ? ((O10>>(4*i))&15) : ((O16>>(4*i))&15);
            v=64'($signed(COS[j*32+:32]))*256+
                (64'($signed(COS[(j+1)*32+:32]))-64'($signed(COS[j*32+:32])))*64'(nlsf[i]&255);
            c[index]=rr(v,4);
        end
        p[0]=65536;qpoly[0]=65536;p[1]=-c[0];qpoly[1]=-c[1];
        for(k=1;k<8;k=k+1)if(k<(in_d>>1))begin
            p[k+1]=2*p[k-1]-rr(64'(c[2*k])*64'(p[k]),16);
            qpoly[k+1]=2*qpoly[k-1]-rr(64'(c[2*k+1])*64'(qpoly[k]),16);
            for(j=0;j<7;j=j+1)begin
                n=k-j;
                if(n>1)begin
                    p[n]=p[n]+p[n-2]-rr(64'(c[2*k])*64'(p[n-1]),16);
                    qpoly[n]=qpoly[n]+qpoly[n-2]-rr(64'(c[2*k+1])*64'(qpoly[n-1]),16);
                end
            end
            p[1]=p[1]-c[2*k];qpoly[1]=qpoly[1]-c[2*k+1];
        end
        for(k=0;k<8;k=k+1)if(k<(in_d>>1))begin
            ps=p[k+1]+p[k];qs=qpoly[k+1]-qpoly[k];
            initial_a[k*32+:32]=-qs-ps;
            initial_a[(in_d-k-1)*32+:32]=qs-ps;
        end
    end
end

localparam IDLE=0,FIT=1,CONVERT=2,CHECK_INIT=3,CHECK=4,EXPAND=5,DONE=6;
logic [2:0] state;
integer order,fit_count,expand_count;
logic fitted;
logic [511:0] a,coefficients,check_a;
integer peak_index,t;
logic signed [63:0] peak,absval,scaled;
integer chirp;
logic [511:0] quantized,init_check,next_check;
logic signed [31:0] dc,rc,factor,new_gain,normal,inv,recip,left,right;
logic signed [63:0] residual,value_l,value_r;
integer bits,check_round,check_level;
logic good;
always_comb begin
    peak=0;peak_index=0;absval=0;scaled=0;chirp=65536;quantized=0;init_check=0;dc=0;
    for(t=0;t<16;t=t+1)if(t<order)begin
        absval=64'($signed(a[t*32+:32]));if(absval<0)absval=-absval;
        if(absval>peak)begin peak=absval;peak_index=t;end
        scaled=rr(64'($signed(a[t*32+:32])),5);
        if(state==CONVERT)begin
            if(scaled>32767)scaled=32767;else if(scaled < -32768)scaled=-32768;
        end
        quantized[t*32+:32]=32'($signed(scaled[15:0]));
        dc=dc+$signed(coefficients[t*32+:32]);
        init_check[t*32+:32]=$signed(coefficients[t*32+:32])<<<12;
    end
    peak=rr(peak,5);
    if(peak>32767)begin
        if(peak>163838)peak=163838;
        chirp=65470-(((peak-32767)<<14)/((peak*(peak_index+1))>>2));
    end
    good=1;next_check=check_a;rc=0;factor=1073741824;new_gain=1073741824;
    normal=0;inv=0;recip=0;residual=0;left=0;right=0;value_l=0;value_r=0;bits=1;check_level=0;
    if(state==CHECK)for(check_round=0;check_round<16;check_round=check_round+1)if(check_round<order && good)begin
        check_level=order-check_round-1;
        value_l=64'($signed(next_check[check_level*32+:32]));
        if(value_l>16773022 || value_l < -16773022)good=0;
        else begin
            rc=-$signed(next_check[check_level*32+:32])*128;
            factor=1073741824-((64'(rc)*64'(rc))>>>32);
            new_gain=((64'(new_gain)*64'(factor))>>>32)*4;
            if(new_gain<107374)good=0;
            else if(check_level>0)begin
                for(t=0;t<31;t=t+1)if(factor>=(32'd1<<t))bits=t+1;
                normal=factor<<<(31-bits);
                inv=536870911/(normal>>>16);
                residual=(64'sd536870912-((64'(normal)*64'(inv))>>>16))<<<3;
                recip=sat32((64'(inv)<<<16)+((residual*64'(inv))>>>16));
                for(t=0;t<8;t=t+1)if(t<((check_level+1)>>1))begin
                    left=$signed(next_check[t*32+:32]);right=$signed(next_check[(check_level-t-1)*32+:32]);
                    value_l=rr(64'(sat32(64'(left)-rr(64'(right)*64'(rc),31)))*64'(recip),bits);
                    value_r=rr(64'(sat32(64'(right)-rr(64'(left)*64'(rc),31)))*64'(recip),bits);
                    if(value_l>2147483647 || value_l < -64'sd2147483648 || value_r>2147483647 || value_r < -64'sd2147483648)good=0;
                    next_check[t*32+:32]=value_l;next_check[(check_level-t-1)*32+:32]=value_r;
                end
            end
        end
    end
end
assign in_ready=state==IDLE;
assign out_valid=state==DONE;
assign out_a_Q12_0=out_valid ? coefficients[0+:16] : 0;
assign out_a_Q12_1=out_valid ? coefficients[32+:16] : 0;
assign out_a_Q12_2=out_valid ? coefficients[64+:16] : 0;
assign out_a_Q12_3=out_valid ? coefficients[96+:16] : 0;
assign out_a_Q12_4=out_valid ? coefficients[128+:16] : 0;
assign out_a_Q12_5=out_valid ? coefficients[160+:16] : 0;
assign out_a_Q12_6=out_valid ? coefficients[192+:16] : 0;
assign out_a_Q12_7=out_valid ? coefficients[224+:16] : 0;
assign out_a_Q12_8=out_valid ? coefficients[256+:16] : 0;
assign out_a_Q12_9=out_valid ? coefficients[288+:16] : 0;
assign out_a_Q12_10=out_valid ? coefficients[320+:16] : 0;
assign out_a_Q12_11=out_valid ? coefficients[352+:16] : 0;
assign out_a_Q12_12=out_valid ? coefficients[384+:16] : 0;
assign out_a_Q12_13=out_valid ? coefficients[416+:16] : 0;
assign out_a_Q12_14=out_valid ? coefficients[448+:16] : 0;
assign out_a_Q12_15=out_valid ? coefficients[480+:16] : 0;

always_ff @(posedge clk)begin
    if(rst)begin
        state<=IDLE;order<=10;a<=0;coefficients<=0;check_a<=0;out_status<=0;
        fit_count<=0;expand_count<=0;fitted<=0;
    end else case(state)
        IDLE:if(in_valid)begin
            order<=in_d;a<=initial_a;coefficients<=0;fit_count<=0;expand_count<=0;fitted<=0;
            out_status<=!(in_d==10 || in_d==16);
            if(in_d==10 || in_d==16)state<=FIT;else state<=DONE;
        end
        FIT:if(peak<=32767)begin fitted<=1;state<=CONVERT;end
            else begin a<=bw(a,order,chirp);fit_count<=fit_count+1;if(fit_count==9)state<=CONVERT;end
        CONVERT:begin
            coefficients<=quantized;
            if(!fitted)for(integer u=0;u<16;u=u+1)a[u*32+:32]<=$signed(quantized[u*32+:32])<<<5;
            state<=CHECK_INIT;
        end
        CHECK_INIT:begin
            check_a<=init_check;
            if(dc>=4096)state<=EXPAND;else state<=CHECK;
        end
        CHECK:if(!good)state<=EXPAND;else state<=DONE;
        EXPAND:begin
            a<=bw(a,order,65536-(2<<expand_count));expand_count<=expand_count+1;state<=CONVERT;
            fitted<=1;
        end
        DONE:if(out_ready)begin state<=IDLE;out_status<=0;end
        default:state<=IDLE;
    endcase
end
endmodule
