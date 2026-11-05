module signal_processing_comm_loop_agc_crcf (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [31:0] in_i,
    input logic [31:0] in_q,
    input logic [31:0] in_bandwidth,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_i,
    output logic [31:0] out_q,
    output logic [31:0] out_gain,
    output logic out_error
);

function automatic logic [63:0] fp_shift_jam(input logic [63:0] x, input integer n);
    if (n <= 0) fp_shift_jam = x;
    else if (n >= 64) fp_shift_jam = {63'b0, |x};
    else fp_shift_jam = (x >> n) | {63'b0, |(x << (64-n))};
endfunction

function automatic logic [31:0] fp_round_pack(
    input logic sign_bit, input integer exponent, input logic [63:0] sig
);
    integer e;
    logic [63:0] shifted, rounded;
    begin
        e = exponent;
        shifted = sig;
        if (e < 1) begin
            shifted = fp_shift_jam(shifted, 1-e);
            e = 1;
        end
        rounded = (shifted >> 3) + {63'b0, (shifted[2] &&
                  (shifted[1] || shifted[0] || shifted[3]))};
        if (rounded[24]) begin
            rounded = rounded >> 1;
            e = e+1;
        end
        if (e >= 255) fp_round_pack = {sign_bit, 8'hff, 23'b0};
        else if (rounded < 64'h800000)
            fp_round_pack = {sign_bit, 8'b0, rounded[22:0]};
        else fp_round_pack = {sign_bit, e[7:0], rounded[22:0]};
    end
endfunction

function automatic logic [31:0] fp32_add_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sa, sb, sign_bit;
    logic [63:0] ma, mb, hi, lo, sum;
    begin
        sa = a[31]; sb = b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
            fp32_add_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000) && (sa != sb))
            fp32_add_bits = 32'h7fc00000;
        else if (a[30:0] == 31'h7f800000) fp32_add_bits = a;
        else if (b[30:0] == 31'h7f800000) fp32_add_bits = b;
        else if ((a[30:0] == 0) && (b[30:0] == 0))
            fp32_add_bits = {sa & sb, 31'b0};
        else begin
            if ((ea > eb) || ((ea == eb) && (ma >= mb))) begin
                hi = ma << 3; lo = fp_shift_jam(mb << 3, ea-eb);
                sign_bit = sa; e = ea;
            end else begin
                hi = mb << 3; lo = fp_shift_jam(ma << 3, eb-ea);
                sign_bit = sb; e = eb;
            end
            sum = sa == sb ? hi+lo : hi-lo;
            if (sum == 0) fp32_add_bits = 32'b0;
            else begin
                if (sum[27]) begin
                    sum = fp_shift_jam(sum, 1); e = e+1;
                end
                for (i=0; i<26; i=i+1) begin
                    if (!sum[26] && e>1) begin sum=sum<<1; e=e-1; end
                end
                fp32_add_bits = fp_round_pack(sign_bit, e, sum);
            end
        end
    end
endfunction

function automatic logic [31:0] fp32_sub_bits(input logic [31:0] a, b);
    fp32_sub_bits = fp32_add_bits(a, b ^ 32'h80000000);
endfunction

function automatic logic [31:0] fp32_mul_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sign_bit;
    logic [63:0] ma, mb, product, sig;
    begin
        sign_bit = a[31] ^ b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000) ||
            ((a[30:0] == 31'h7f800000) && (b[30:0] == 0)) ||
            ((b[30:0] == 31'h7f800000) && (a[30:0] == 0)))
            fp32_mul_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) || (b[30:0] == 31'h7f800000))
            fp32_mul_bits = {sign_bit, 8'hff, 23'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 0))
            fp32_mul_bits = {sign_bit, 31'b0};
        else begin
            for (i=0; i<23; i=i+1) begin
                if (!ma[23]) begin ma=ma<<1; ea=ea-1; end
                if (!mb[23]) begin mb=mb<<1; eb=eb-1; end
            end
            product = ma*mb;
            e = ea+eb-127;
            if (product[47]) begin sig=fp_shift_jam(product,21); e=e+1; end
            else sig=fp_shift_jam(product,20);
            fp32_mul_bits = fp_round_pack(sign_bit, e, sig);
        end
    end
endfunction

    localparam logic signed [127:0] Q48 = 128'sd281474976710656;
    localparam logic signed [127:0] LN2 = 128'sd195103586505167;

    function automatic logic signed [63:0] logarithm_q32(input logic [31:0] x);
        logic signed [127:0] m,w,square,power,total;
        integer e;
        begin
            e = int'(x[30:23]) - 127;
            m = Q48 + (128'(x[22:0]) << 25);
            w = ((m-Q48) <<< 48)/(m+Q48);
            square = (w*w) >>> 48;
            power = w; total = 0;
            for (int j=0;j<9;j++) begin
                total += power/(2*j+1);
                power = (power*square) >>> 48;
            end
            logarithm_q32 = (e*LN2 + 2*total) >>> 16;
        end
    endfunction

    function automatic logic signed [63:0] bandwidth_q32(input logic [31:0] x);
        logic [63:0] mantissa;
        integer shift;
        begin
            shift = int'(x[30:23])-118;
            mantissa = {1'b1,x[22:0]};
            if (x[30:23]==0) bandwidth_q32=0;
            else if (shift>=0) bandwidth_q32=mantissa<<shift;
            else if (shift<=-64) bandwidth_q32=0;
            else bandwidth_q32=mantissa>>(-shift);
        end
    endfunction

    function automatic logic [31:0] exponential_q32(input logic signed [63:0] z);
        logic signed [127:0] z48,k,r,term,total,mantissa,tail;
        begin
            z48 = 128'(z) <<< 16;
            k = z48>=0 ? z48/LN2 : (z48-LN2+1)/LN2;
            r = z48-k*LN2; term=Q48; total=Q48;
            for (int j=1;j<=18;j++) begin
                term=((term*r) >>> 48)/j;
                total+=term;
            end
            mantissa=total >>> 25; tail=total & 128'sd33554431;
            if (tail>128'sd16777216 || (tail==128'sd16777216 && mantissa[0]))
                mantissa+=1;
            if (mantissa>=128'sd16777216) begin
                mantissa=mantissa >>> 1; k+=1;
            end
            exponential_q32 = ((k+127)<<23) | (mantissa & 128'sh7fffff);
        end
    endfunction

    function automatic logic [31:0] updated_gain(input logic [31:0] g,e,a);
        logic signed [127:0] numerator, aq, lq;
        logic signed [63:0] z;
        logic [31:0] result;
        begin
            updated_gain=g;
            if (e>32'h358637bd) begin
                aq=bandwidth_q32(a);
                lq=logarithm_q32(e);
                numerator=-(aq*lq);
                z=numerator/128'sd8589934592;
                result=fp32_mul_bits(g,exponential_q32(z));
                updated_gain=result>32'h49742400 ? 32'h49742400 : result;
            end
        end
    endfunction

    logic occupied;
    logic [31:0] gain,energy,alpha,y0,y1,power,energy_next,gain_next;
    logic invalid;
    assign in_ready=!occupied || out_ready;
    assign out_valid=occupied;
    always_comb begin
        alpha=in_bandwidth==32'h80000000 ? 32'd0 : in_bandwidth;
        y0=fp32_mul_bits(in_i,gain);
        y1=fp32_mul_bits(in_q,gain);
        power=fp32_add_bits(fp32_mul_bits(y0,y0),fp32_mul_bits(y1,y1));
        energy_next=fp32_add_bits(fp32_mul_bits(fp32_sub_bits(32'h3f800000,alpha),energy),fp32_mul_bits(alpha,power));
        invalid=alpha>32'h3f800000 || in_i[30:23]==8'hff || in_q[30:23]==8'hff || energy_next[30:23]==8'hff;
        gain_next=gain;
        if (!invalid) gain_next=updated_gain(gain,energy_next,alpha);
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            occupied<=0;gain<=32'h3f800000;energy<=32'h3f800000;
            out_i<=0;
            out_q<=0;
            out_gain<=0;
            out_error<=0;
        end else if (in_valid && in_ready) begin
            occupied<=1;out_error<=invalid;out_gain<=gain_next;
            out_i<=invalid ? 32'd0 : y0;
            out_q<=invalid ? 32'd0 : y1;
            if (!invalid) begin gain<=gain_next;energy<=energy_next;end
        end else if (out_ready) occupied<=0;
    end
endmodule
