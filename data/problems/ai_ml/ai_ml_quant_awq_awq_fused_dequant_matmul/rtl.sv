module AWQFusedDequantMatMul (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [31:0] in_x0,
    input logic [31:0] in_x1,
    input logic [31:0] in_x2,
    input logic [31:0] in_x3,
    input logic [31:0] in_w00,
    input logic [31:0] in_w01,
    input logic [31:0] in_w02,
    input logic [31:0] in_w03,
    input logic [31:0] in_w10,
    input logic [31:0] in_w11,
    input logic [31:0] in_w12,
    input logic [31:0] in_w13,
    input logic [31:0] in_w20,
    input logic [31:0] in_w21,
    input logic [31:0] in_w22,
    input logic [31:0] in_w23,
    input logic [31:0] in_w30,
    input logic [31:0] in_w31,
    input logic [31:0] in_w32,
    input logic [31:0] in_w33,
    input logic [31:0] in_s0,
    input logic [31:0] in_s1,
    input logic [31:0] in_s2,
    input logic [31:0] in_s3,
    input logic out_ready,
    output logic out_valid,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3,
    output logic [31:0] out_loss,
    output logic [63:0] out_qweight,
    output logic [127:0] out_quant_scale,
    output logic [0:0] out_error
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

function automatic logic [31:0] fp32_div_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sign_bit;
    logic [63:0] ma, mb, numerator, quotient, remainder;
    begin
        sign_bit = a[31] ^ b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000) ||
            ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000)) ||
            ((a[30:0] == 0) && (b[30:0] == 0)))
            fp32_div_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) || (b[30:0] == 0))
            fp32_div_bits = {sign_bit, 8'hff, 23'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 31'h7f800000))
            fp32_div_bits = {sign_bit, 31'b0};
        else begin
            for (i=0; i<23; i=i+1) begin
                if (!ma[23]) begin ma=ma<<1; ea=ea-1; end
                if (!mb[23]) begin mb=mb<<1; eb=eb-1; end
            end
            e = ea-eb+127;
            if (ma < mb) begin ma=ma<<1; e=e-1; end
            numerator = ma<<26;
            quotient = numerator/mb;
            remainder = numerator%mb;
            quotient = quotient | {63'b0, (remainder != 0)};
            fp32_div_bits = fp_round_pack(sign_bit, e, quotient);
        end
    end
endfunction

    function automatic logic signed [1:0] fp32_cmp_bits(input logic [31:0] a, b);
    if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
        fp32_cmp_bits = 2'sb10; // -2: unordered, never an ordered <= result.
    else if ((a == b) || ((a[30:0] == 0) && (b[30:0] == 0))) fp32_cmp_bits = 0;
    else if (a[31] != b[31]) fp32_cmp_bits = a[31] ? -2'sd1 : 2'sd1;
    else if ((a[30:0] < b[30:0]) != a[31]) fp32_cmp_bits = -2'sd1;
    else fp32_cmp_bits = 2'sd1;
endfunction
function automatic logic greater(input logic [31:0] a, b);
    greater = fp32_cmp_bits(a,b) == 2'sd1;
endfunction
    function automatic integer round_integer(input logic [31:0] word);
        integer exponent,shift,integral;
        logic [31:0] mantissa,remainder,halfway;
        begin
            exponent=int'(word[30:23]); shift=0; integral=0;
            mantissa=0;remainder=0;halfway=0;
            if (exponent>=126) begin
                shift=150-exponent;mantissa={8'd0,1'b1,word[22:0]};
                integral=int'(mantissa>>shift);
                remainder=mantissa & ((32'd1<<shift)-1);halfway=32'd1<<(shift-1);
                if (remainder>halfway || (remainder==halfway && integral[0])) integral=integral+1;
            end
            round_integer=word[31] ? -integral : integral;
        end
    endfunction
    function automatic logic [31:0] integer_word(input integer value);
        begin
            case(value)
                -7:integer_word=32'hc0e00000;
                -6:integer_word=32'hc0c00000;
                -5:integer_word=32'hc0a00000;
                -4:integer_word=32'hc0800000;
                -3:integer_word=32'hc0400000;
                -2:integer_word=32'hc0000000;
                -1:integer_word=32'hbf800000;
                0:integer_word=32'h00000000;
                1:integer_word=32'h3f800000;
                2:integer_word=32'h40000000;
                3:integer_word=32'h40400000;
                4:integer_word=32'h40800000;
                5:integer_word=32'h40a00000;
                6:integer_word=32'h40c00000;
                7:integer_word=32'h40e00000;
                default:integer_word=0;
            endcase
        end
    endfunction
    logic [31:0] x[0:3], w[0:15], s[0:3], scaled_x[0:3], scaled_w[0:3], y[0:3];
    logic [31:0] peak,scale,ratio,dequant,original,error_value,loss;
    logic bad;
    integer quant;
    logic [31:0] next_out_y0;
    logic [31:0] next_out_y1;
    logic [31:0] next_out_y2;
    logic [31:0] next_out_y3;
    logic [31:0] next_out_loss;
    logic [63:0] next_out_qweight;
    logic [127:0] next_out_quant_scale;
    logic [0:0] next_out_error;
    always_comb begin
        x[0]=in_x0;
        x[1]=in_x1;
        x[2]=in_x2;
        x[3]=in_x3;
        w[0]=in_w00;
        w[1]=in_w01;
        w[2]=in_w02;
        w[3]=in_w03;
        w[4]=in_w10;
        w[5]=in_w11;
        w[6]=in_w12;
        w[7]=in_w13;
        w[8]=in_w20;
        w[9]=in_w21;
        w[10]=in_w22;
        w[11]=in_w23;
        w[12]=in_w30;
        w[13]=in_w31;
        w[14]=in_w32;
        w[15]=in_w33;
        s[0]=in_s0;
        s[1]=in_s1;
        s[2]=in_s2;
        s[3]=in_s3;

        bad=0;peak=0;scale=0;ratio=0;dequant=0;original=0;error_value=0;loss=0;quant=0;
        next_out_qweight=0;next_out_quant_scale=0;
        for(int j=0;j<4;j++) begin
            if (x[j][30:23]==255 || s[j][31] || s[j][30:23]==0 || s[j][30:23]==255) bad=1;
            scaled_x[j]=fp32_div_bits(x[j],s[j]);
            if (scaled_x[j][30:23]==255) bad=1;
            scaled_w[j]=0;y[j]=0;
        end
        for(int i=0;i<4;i++) begin
            peak=0;original=0;
            for(int j=0;j<4;j++) begin
                if(w[4*i+j][30:23]==255) bad=1;
                scaled_w[j]=fp32_mul_bits(w[4*i+j],s[j]);
                if(scaled_w[j][30:23]==255) bad=1;
                if(greater(scaled_w[j]&32'h7fffffff,peak)) peak=scaled_w[j]&32'h7fffffff;
            end
            scale=peak==0 ? 32'h3f800000 : fp32_div_bits(peak,32'h40e00000);
            if (peak!=0 && scale[30:23]==0) bad=1;
            next_out_quant_scale[32*i +:32]=scale;
            for(int j=0;j<4;j++) begin
                ratio=fp32_div_bits(scaled_w[j],scale);
                if(ratio[30:23]==255) begin bad=1;quant=0;end
                else if((ratio&32'h7fffffff)>=32'h40e00000) quant=ratio[31] ? -7 : 7;
                else quant=round_integer(ratio);
                next_out_qweight[4*(4*i+j) +:4]=4'(quant);
                dequant=fp32_mul_bits(integer_word(quant),scale);
                y[i]=fp32_add_bits(y[i],fp32_mul_bits(scaled_x[j],dequant));
                original=fp32_add_bits(original,fp32_mul_bits(x[j],w[4*i+j]));
            end
            if(y[i][30:23]==255) bad=1;
            error_value=fp32_add_bits(original,y[i]^32'h80000000);
            loss=fp32_add_bits(loss,fp32_mul_bits(error_value,error_value));
        end
        loss=fp32_mul_bits(loss,32'h3e800000);
        if(loss[30:23]==255) bad=1;
        next_out_error=bad;next_out_loss=bad ? 0 : loss;
        if(bad) begin next_out_qweight=0;next_out_quant_scale=0;end
        next_out_y0=bad ? 0 : y[0];
        next_out_y1=bad ? 0 : y[1];
        next_out_y2=bad ? 0 : y[2];
        next_out_y3=bad ? 0 : y[3];
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin out_valid <= 0; out_y0 <= 0; out_y1 <= 0; out_y2 <= 0; out_y3 <= 0; out_loss <= 0; out_qweight <= 0; out_quant_scale <= 0; out_error <= 0; end
        else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin out_y0 <= next_out_y0; out_y1 <= next_out_y1; out_y2 <= next_out_y2; out_y3 <= next_out_y3; out_loss <= next_out_loss; out_qweight <= next_out_qweight; out_quant_scale <= next_out_quant_scale; out_error <= next_out_error; end
            else begin out_y0 <= 0; out_y1 <= 0; out_y2 <= 0; out_y3 <= 0; out_loss <= 0; out_qweight <= 0; out_quant_scale <= 0; out_error <= 0; end
        end
    end
endmodule
