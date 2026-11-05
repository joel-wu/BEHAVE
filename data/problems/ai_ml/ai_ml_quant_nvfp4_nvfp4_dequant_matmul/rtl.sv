module ai_ml_quant_nvfp4_nvfp4_dequant_matmul (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [63:0] in_a,
    input logic [63:0] in_b,
    input logic [7:0] in_scale_a,
    input logic [7:0] in_scale_b,
    input logic [31:0] in_global_a,
    input logic [31:0] in_global_b,
    input logic out_ready,
    output logic out_valid,
    output logic [31:0] out_y,
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

    function automatic [31:0] fp8_word(input integer k);
        case(k)
            0:fp8_word=32'h00000000;
            1:fp8_word=32'h3b000000;
            2:fp8_word=32'h3b800000;
            3:fp8_word=32'h3bc00000;
            4:fp8_word=32'h3c000000;
            5:fp8_word=32'h3c200000;
            6:fp8_word=32'h3c400000;
            7:fp8_word=32'h3c600000;
            8:fp8_word=32'h3c800000;
            9:fp8_word=32'h3c900000;
            10:fp8_word=32'h3ca00000;
            11:fp8_word=32'h3cb00000;
            12:fp8_word=32'h3cc00000;
            13:fp8_word=32'h3cd00000;
            14:fp8_word=32'h3ce00000;
            15:fp8_word=32'h3cf00000;
            16:fp8_word=32'h3d000000;
            17:fp8_word=32'h3d100000;
            18:fp8_word=32'h3d200000;
            19:fp8_word=32'h3d300000;
            20:fp8_word=32'h3d400000;
            21:fp8_word=32'h3d500000;
            22:fp8_word=32'h3d600000;
            23:fp8_word=32'h3d700000;
            24:fp8_word=32'h3d800000;
            25:fp8_word=32'h3d900000;
            26:fp8_word=32'h3da00000;
            27:fp8_word=32'h3db00000;
            28:fp8_word=32'h3dc00000;
            29:fp8_word=32'h3dd00000;
            30:fp8_word=32'h3de00000;
            31:fp8_word=32'h3df00000;
            32:fp8_word=32'h3e000000;
            33:fp8_word=32'h3e100000;
            34:fp8_word=32'h3e200000;
            35:fp8_word=32'h3e300000;
            36:fp8_word=32'h3e400000;
            37:fp8_word=32'h3e500000;
            38:fp8_word=32'h3e600000;
            39:fp8_word=32'h3e700000;
            40:fp8_word=32'h3e800000;
            41:fp8_word=32'h3e900000;
            42:fp8_word=32'h3ea00000;
            43:fp8_word=32'h3eb00000;
            44:fp8_word=32'h3ec00000;
            45:fp8_word=32'h3ed00000;
            46:fp8_word=32'h3ee00000;
            47:fp8_word=32'h3ef00000;
            48:fp8_word=32'h3f000000;
            49:fp8_word=32'h3f100000;
            50:fp8_word=32'h3f200000;
            51:fp8_word=32'h3f300000;
            52:fp8_word=32'h3f400000;
            53:fp8_word=32'h3f500000;
            54:fp8_word=32'h3f600000;
            55:fp8_word=32'h3f700000;
            56:fp8_word=32'h3f800000;
            57:fp8_word=32'h3f900000;
            58:fp8_word=32'h3fa00000;
            59:fp8_word=32'h3fb00000;
            60:fp8_word=32'h3fc00000;
            61:fp8_word=32'h3fd00000;
            62:fp8_word=32'h3fe00000;
            63:fp8_word=32'h3ff00000;
            64:fp8_word=32'h40000000;
            65:fp8_word=32'h40100000;
            66:fp8_word=32'h40200000;
            67:fp8_word=32'h40300000;
            68:fp8_word=32'h40400000;
            69:fp8_word=32'h40500000;
            70:fp8_word=32'h40600000;
            71:fp8_word=32'h40700000;
            72:fp8_word=32'h40800000;
            73:fp8_word=32'h40900000;
            74:fp8_word=32'h40a00000;
            75:fp8_word=32'h40b00000;
            76:fp8_word=32'h40c00000;
            77:fp8_word=32'h40d00000;
            78:fp8_word=32'h40e00000;
            79:fp8_word=32'h40f00000;
            80:fp8_word=32'h41000000;
            81:fp8_word=32'h41100000;
            82:fp8_word=32'h41200000;
            83:fp8_word=32'h41300000;
            84:fp8_word=32'h41400000;
            85:fp8_word=32'h41500000;
            86:fp8_word=32'h41600000;
            87:fp8_word=32'h41700000;
            88:fp8_word=32'h41800000;
            89:fp8_word=32'h41900000;
            90:fp8_word=32'h41a00000;
            91:fp8_word=32'h41b00000;
            92:fp8_word=32'h41c00000;
            93:fp8_word=32'h41d00000;
            94:fp8_word=32'h41e00000;
            95:fp8_word=32'h41f00000;
            96:fp8_word=32'h42000000;
            97:fp8_word=32'h42100000;
            98:fp8_word=32'h42200000;
            99:fp8_word=32'h42300000;
            100:fp8_word=32'h42400000;
            101:fp8_word=32'h42500000;
            102:fp8_word=32'h42600000;
            103:fp8_word=32'h42700000;
            104:fp8_word=32'h42800000;
            105:fp8_word=32'h42900000;
            106:fp8_word=32'h42a00000;
            107:fp8_word=32'h42b00000;
            108:fp8_word=32'h42c00000;
            109:fp8_word=32'h42d00000;
            110:fp8_word=32'h42e00000;
            111:fp8_word=32'h42f00000;
            112:fp8_word=32'h43000000;
            113:fp8_word=32'h43100000;
            114:fp8_word=32'h43200000;
            115:fp8_word=32'h43300000;
            116:fp8_word=32'h43400000;
            117:fp8_word=32'h43500000;
            118:fp8_word=32'h43600000;
            119:fp8_word=32'h43700000;
            120:fp8_word=32'h43800000;
            121:fp8_word=32'h43900000;
            122:fp8_word=32'h43a00000;
            123:fp8_word=32'h43b00000;
            124:fp8_word=32'h43c00000;
            125:fp8_word=32'h43d00000;
            126:fp8_word=32'h43e00000;
            default:fp8_word=0;
        endcase
    endfunction
    logic [31:0] next_out_y;
    logic [0:0] next_out_error;
    function automatic [31:0] fp4_word(input integer k);
        case(k)
            0:fp4_word=32'h00000000;
            1:fp4_word=32'h3f000000;
            2:fp4_word=32'h3f800000;
            3:fp4_word=32'h3fc00000;
            4:fp4_word=32'h40000000;
            5:fp4_word=32'h40400000;
            6:fp4_word=32'h40800000;
            7:fp4_word=32'h40c00000;
            8:fp4_word=32'h00000000;
            9:fp4_word=32'hbf000000;
            10:fp4_word=32'hbf800000;
            11:fp4_word=32'hbfc00000;
            12:fp4_word=32'hc0000000;
            13:fp4_word=32'hc0400000;
            14:fp4_word=32'hc0800000;
            15:fp4_word=32'hc0c00000;
            default:fp4_word=0;
        endcase
    endfunction
    logic [31:0] sa,sb,a,b,term,value;
    logic bad;
    always_comb begin
        bad=in_scale_a<1 || in_scale_a>126 || in_scale_b<1 || in_scale_b>126;
        bad=bad || in_global_a[31] || in_global_a[30:23]==0 || in_global_a[30:23]==255;
        bad=bad || in_global_b[31] || in_global_b[30:23]==0 || in_global_b[30:23]==255;
        sa=fp32_mul_bits(fp8_word(in_scale_a>=1 && in_scale_a<=126 ? int'(in_scale_a) : 1),in_global_a);
        sb=fp32_mul_bits(fp8_word(in_scale_b>=1 && in_scale_b<=126 ? int'(in_scale_b) : 1),in_global_b);
        if(sa[31] || sa[30:23]==0 || sa[30:23]==255 || sb[31] || sb[30:23]==0 || sb[30:23]==255) bad=1;
        a=0;b=0;term=0;value=0;
        for(int i=0;i<16;i++) begin
            a=fp32_mul_bits(fp4_word(int'(in_a[4*i +:4])),sa);
            b=fp32_mul_bits(fp4_word(int'(in_b[4*i +:4])),sb);
            term=fp32_mul_bits(a,b);value=fp32_add_bits(value,term);
            if(a[30:23]==255 || b[30:23]==255 || term[30:23]==255 || value[30:23]==255) bad=1;
        end
        next_out_y=bad ? 0 : value;next_out_error=bad;
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin out_valid <= 0; out_y <= 0; out_error <= 0; end
        else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin out_y <= next_out_y; out_error <= next_out_error; end
            else begin out_y <= 0; out_error <= 0; end
        end
    end
endmodule
