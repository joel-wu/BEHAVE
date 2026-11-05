module Int8PerChannelQuant (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [127:0] in_input,
    input logic [63:0] in_scale,
    input logic [15:0] in_zero_point,
    input logic [7:0] in_quant_min, in_quant_max,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_quantized,
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
    function automatic logic [31:0] fp32_is_nan(input logic [31:0] x);
        logic [7:0] expf;
        logic [22:0] fracf;
        begin
            expf = x[30:23];
            fracf = x[22:0];
            if ((expf == 8'hff) && (fracf != 23'h0)) fp32_is_nan = 32'd1;
            else fp32_is_nan = 32'd0;
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
    logic [31:0] next_out_quantized,scale,inverse,value,scaled;
    logic next_out_error;
    integer qmin,qmax,zero,quant,lane;
    always_comb begin
        qmin=int'($signed(in_quant_min));qmax=int'($signed(in_quant_max));
        next_out_quantized=0;next_out_error=qmin>qmax;
        scale=0;inverse=0;value=0;scaled=0;zero=0;quant=0;lane=0;
        for (int channel=0;channel<2;channel++) begin
            scale=in_scale[32*channel +: 32];zero=int'($signed(in_zero_point[8*channel +: 8]));
            if (scale[31] || scale[30:23]==0 || scale[30:23]==255) next_out_error=1;
            inverse=fp32_div_bits(32'h3f800000,scale);
            for (int sample_index=0;sample_index<2;sample_index++) begin
                lane=2*channel+sample_index;value=in_input[32*lane +: 32];
                if (value[30:23]==255) next_out_error=1;
                scaled=fp32_mul_bits(value,inverse);
                if (fp32_is_nan(scaled)) begin next_out_error=1;quant=0;end
                else if (!greater(32'h43800000,scaled)) quant=qmax;
                else if (!greater(scaled,32'hc3800000)) quant=qmin;
                else begin
                    quant=round_integer(scaled)+zero;
                    if (quant<qmin) quant=qmin;
                    if (quant>qmax) quant=qmax;
                end
                next_out_quantized[8*lane +: 8]=8'(quant);
            end
        end
        if (next_out_error) next_out_quantized=0;
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin out_valid <= 0; out_quantized <= 0; out_error <= 0; end
        else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin out_quantized <= next_out_quantized; out_error <= next_out_error; end
            else begin out_quantized <= 0; out_error <= 0; end
        end
    end
endmodule
