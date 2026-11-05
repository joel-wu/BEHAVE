module sin_cos_degrees_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_theta,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_sin,
    output logic [31:0] out_cos
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

localparam logic [31:0] INV_360         = 32'h3B360B61;
localparam logic [31:0] TABLE_SIZE_FP32 = 32'h44000000;
localparam logic [31:0] DN              = 32'h3C490FDB;
localparam logic [31:0] TWO             = 32'h40000000;
localparam logic [31:0] THREE           = 32'h40400000;
localparam logic [31:0] ZERO            = 32'h00000000;
localparam logic [31:0] CANON_NAN       = 32'h7FC00000;

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic fp32_is_nan(input logic [31:0] a);
    logic [7:0] expa;
    logic [22:0] fraca;
    begin
        expa = a[30:23];
        fraca = a[22:0];
        fp32_is_nan = (expa == 8'hFF) && (fraca != 23'd0);
    end
endfunction

function automatic logic fp32_is_inf(input logic [31:0] a);
    logic [7:0] expa;
    logic [22:0] fraca;
    begin
        expa = a[30:23];
        fraca = a[22:0];
        fp32_is_inf = (expa == 8'hFF) && (fraca == 23'd0);
    end
endfunction

function automatic logic [31:0] fp32_neg(input logic [31:0] x);
    fp32_neg = x ^ 32'h80000000;
endfunction

function automatic logic signed [1:0] fp32_cmp(input logic [31:0] a, b);
    if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
        fp32_cmp = 2'sb10; // -2: unordered, never an ordered <= result.
    else if ((a == b) || ((a[30:0] == 0) && (b[30:0] == 0))) fp32_cmp = 0;
    else if (a[31] != b[31]) fp32_cmp = a[31] ? -2'sd1 : 2'sd1;
    else if ((a[30:0] < b[30:0]) != a[31]) fp32_cmp = -2'sd1;
    else fp32_cmp = 2'sd1;
endfunction

function automatic logic [31:0] trunc_to_u32_bits(input logic [31:0] x);
    logic [7:0] expv;
    logic [22:0] fracv;
    logic signed [8:0] e;
    logic [23:0] mant;
    logic [31:0] tmp;
    begin
        expv = x[30:23];
        fracv = x[22:0];
        trunc_to_u32_bits = 32'd0;
        if (expv == 8'd0) begin
            trunc_to_u32_bits = 32'd0;
        end else begin
            e = $signed({1'b0, expv}) - 9'sd127;
            mant = {1'b1, fracv};
            if (e < 0) begin
                trunc_to_u32_bits = 32'd0;
            end else if (e >= 23) begin
                tmp = mant;
                trunc_to_u32_bits = tmp << (e - 23);
            end else begin
                trunc_to_u32_bits = mant >> (23 - e);
            end
        end
    end
endfunction

function automatic logic [31:0] u32_to_fp32_bits(input logic [31:0] x);
    integer i;
    integer msb;
    logic found;
    logic [31:0] shifted;
    logic [7:0] expv;
    begin
        u32_to_fp32_bits = 32'd0;
        if (x == 32'd0) begin
            u32_to_fp32_bits = 32'd0;
        end else begin
            msb = 31;
            found = 1'b0;
            for (i = 31; i >= 0; i = i - 1) begin
                if ((!found) && x[i]) begin
                    msb = i;
                    found = 1'b1;
                end
            end
            expv = msb + 127;
            if (msb <= 23) begin
                shifted = x << (23 - msb);
                u32_to_fp32_bits = {1'b0, expv, shifted[22:0]};
            end else begin
                shifted = x >> (msb - 23);
                u32_to_fp32_bits = {1'b0, expv, shifted[22:0]};
            end
        end
    end
endfunction

function automatic logic [31:0] sin_table_f32(input logic [9:0] idx);
    begin
        unique case (idx)
            10'd0: sin_table_f32 = 32'h00000000;
            10'd1: sin_table_f32 = 32'h3C490E92;
            10'd2: sin_table_f32 = 32'h3CC90AB1;
            10'd3: sin_table_f32 = 32'h3D16C32B;
            10'd4: sin_table_f32 = 32'h3D48FB2E;
            10'd5: sin_table_f32 = 32'h3D7B2B75;
            10'd6: sin_table_f32 = 32'h3D96A904;
            10'd7: sin_table_f32 = 32'h3DAFB680;
            10'd8: sin_table_f32 = 32'h3DC8BD36;
            10'd9: sin_table_f32 = 32'h3DE1BC2F;
            10'd10: sin_table_f32 = 32'h3DFAB273;
            10'd11: sin_table_f32 = 32'h3E09CF87;
            10'd12: sin_table_f32 = 32'h3E164083;
            10'd13: sin_table_f32 = 32'h3E22ABB5;
            10'd14: sin_table_f32 = 32'h3E2F10A2;
            10'd15: sin_table_f32 = 32'h3E3B6ECF;
            10'd16: sin_table_f32 = 32'h3E47C5C2;
            10'd17: sin_table_f32 = 32'h3E541501;
            10'd18: sin_table_f32 = 32'h3E605C13;
            10'd19: sin_table_f32 = 32'h3E6C9A7F;
            10'd20: sin_table_f32 = 32'h3E78CFCC;
            10'd21: sin_table_f32 = 32'h3E827DC0;
            10'd22: sin_table_f32 = 32'h3E888E93;
            10'd23: sin_table_f32 = 32'h3E8E9A22;
            10'd24: sin_table_f32 = 32'h3E94A032;
            10'd25: sin_table_f32 = 32'h3E9AA086;
            10'd26: sin_table_f32 = 32'h3EA09AE5;
            10'd27: sin_table_f32 = 32'h3EA68F12;
            10'd28: sin_table_f32 = 32'h3EAC7CD4;
            10'd29: sin_table_f32 = 32'h3EB263EF;
            10'd30: sin_table_f32 = 32'h3EB8442A;
            10'd31: sin_table_f32 = 32'h3EBE1D49;
            10'd32: sin_table_f32 = 32'h3EC3EF15;
            10'd33: sin_table_f32 = 32'h3EC9B953;
            10'd34: sin_table_f32 = 32'h3ECF7BCA;
            10'd35: sin_table_f32 = 32'h3ED53641;
            10'd36: sin_table_f32 = 32'h3EDAE880;
            10'd37: sin_table_f32 = 32'h3EE0924F;
            10'd38: sin_table_f32 = 32'h3EE63375;
            10'd39: sin_table_f32 = 32'h3EEBCBBB;
            10'd40: sin_table_f32 = 32'h3EF15AEA;
            10'd41: sin_table_f32 = 32'h3EF6E0CB;
            10'd42: sin_table_f32 = 32'h3EFC5D27;
            10'd43: sin_table_f32 = 32'h3F00E7E4;
            10'd44: sin_table_f32 = 32'h3F039C3D;
            10'd45: sin_table_f32 = 32'h3F064B82;
            10'd46: sin_table_f32 = 32'h3F08F59B;
            10'd47: sin_table_f32 = 32'h3F0B9A6B;
            10'd48: sin_table_f32 = 32'h3F0E39DA;
            10'd49: sin_table_f32 = 32'h3F10D3CD;
            10'd50: sin_table_f32 = 32'h3F13682A;
            10'd51: sin_table_f32 = 32'h3F15F6D9;
            10'd52: sin_table_f32 = 32'h3F187FC0;
            10'd53: sin_table_f32 = 32'h3F1B02C6;
            10'd54: sin_table_f32 = 32'h3F1D7FD1;
            10'd55: sin_table_f32 = 32'h3F1FF6CB;
            10'd56: sin_table_f32 = 32'h3F226799;
            10'd57: sin_table_f32 = 32'h3F24D225;
            10'd58: sin_table_f32 = 32'h3F273656;
            10'd59: sin_table_f32 = 32'h3F299415;
            10'd60: sin_table_f32 = 32'h3F2BEB4A;
            10'd61: sin_table_f32 = 32'h3F2E3BDE;
            10'd62: sin_table_f32 = 32'h3F3085BB;
            10'd63: sin_table_f32 = 32'h3F32C8C9;
            10'd64: sin_table_f32 = 32'h3F3504F3;
            10'd65: sin_table_f32 = 32'h3F373A23;
            10'd66: sin_table_f32 = 32'h3F396842;
            10'd67: sin_table_f32 = 32'h3F3B8F3B;
            10'd68: sin_table_f32 = 32'h3F3DAEF9;
            10'd69: sin_table_f32 = 32'h3F3FC767;
            10'd70: sin_table_f32 = 32'h3F41D870;
            10'd71: sin_table_f32 = 32'h3F43E201;
            10'd72: sin_table_f32 = 32'h3F45E403;
            10'd73: sin_table_f32 = 32'h3F47DE65;
            10'd74: sin_table_f32 = 32'h3F49D112;
            10'd75: sin_table_f32 = 32'h3F4BBBF8;
            10'd76: sin_table_f32 = 32'h3F4D9F02;
            10'd77: sin_table_f32 = 32'h3F4F7A20;
            10'd78: sin_table_f32 = 32'h3F514D3D;
            10'd79: sin_table_f32 = 32'h3F531849;
            10'd80: sin_table_f32 = 32'h3F54DB31;
            10'd81: sin_table_f32 = 32'h3F5695E5;
            10'd82: sin_table_f32 = 32'h3F584853;
            10'd83: sin_table_f32 = 32'h3F59F26A;
            10'd84: sin_table_f32 = 32'h3F5B941A;
            10'd85: sin_table_f32 = 32'h3F5D2D53;
            10'd86: sin_table_f32 = 32'h3F5EBE05;
            10'd87: sin_table_f32 = 32'h3F604621;
            10'd88: sin_table_f32 = 32'h3F61C597;
            10'd89: sin_table_f32 = 32'h3F633C5A;
            10'd90: sin_table_f32 = 32'h3F64AA59;
            10'd91: sin_table_f32 = 32'h3F660F88;
            10'd92: sin_table_f32 = 32'h3F676BD8;
            10'd93: sin_table_f32 = 32'h3F68BF3C;
            10'd94: sin_table_f32 = 32'h3F6A09A7;
            10'd95: sin_table_f32 = 32'h3F6B4B0C;
            10'd96: sin_table_f32 = 32'h3F6C835E;
            10'd97: sin_table_f32 = 32'h3F6DB293;
            10'd98: sin_table_f32 = 32'h3F6ED89E;
            10'd99: sin_table_f32 = 32'h3F6FF573;
            10'd100: sin_table_f32 = 32'h3F710908;
            10'd101: sin_table_f32 = 32'h3F721352;
            10'd102: sin_table_f32 = 32'h3F731447;
            10'd103: sin_table_f32 = 32'h3F740BDD;
            10'd104: sin_table_f32 = 32'h3F74FA0B;
            10'd105: sin_table_f32 = 32'h3F75DEC6;
            10'd106: sin_table_f32 = 32'h3F76BA07;
            10'd107: sin_table_f32 = 32'h3F778BC5;
            10'd108: sin_table_f32 = 32'h3F7853F8;
            10'd109: sin_table_f32 = 32'h3F791298;
            10'd110: sin_table_f32 = 32'h3F79C79D;
            10'd111: sin_table_f32 = 32'h3F7A7302;
            10'd112: sin_table_f32 = 32'h3F7B14BE;
            10'd113: sin_table_f32 = 32'h3F7BACCD;
            10'd114: sin_table_f32 = 32'h3F7C3B28;
            10'd115: sin_table_f32 = 32'h3F7CBFC9;
            10'd116: sin_table_f32 = 32'h3F7D3AAC;
            10'd117: sin_table_f32 = 32'h3F7DABCC;
            10'd118: sin_table_f32 = 32'h3F7E1323;
            10'd119: sin_table_f32 = 32'h3F7E70B0;
            10'd120: sin_table_f32 = 32'h3F7EC46D;
            10'd121: sin_table_f32 = 32'h3F7F0E58;
            10'd122: sin_table_f32 = 32'h3F7F4E6D;
            10'd123: sin_table_f32 = 32'h3F7F84AB;
            10'd124: sin_table_f32 = 32'h3F7FB10F;
            10'd125: sin_table_f32 = 32'h3F7FD397;
            10'd126: sin_table_f32 = 32'h3F7FEC43;
            10'd127: sin_table_f32 = 32'h3F7FFB11;
            10'd128: sin_table_f32 = 32'h3F800000;
            10'd129: sin_table_f32 = 32'h3F7FFB11;
            10'd130: sin_table_f32 = 32'h3F7FEC43;
            10'd131: sin_table_f32 = 32'h3F7FD397;
            10'd132: sin_table_f32 = 32'h3F7FB10F;
            10'd133: sin_table_f32 = 32'h3F7F84AB;
            10'd134: sin_table_f32 = 32'h3F7F4E6D;
            10'd135: sin_table_f32 = 32'h3F7F0E58;
            10'd136: sin_table_f32 = 32'h3F7EC46D;
            10'd137: sin_table_f32 = 32'h3F7E70B0;
            10'd138: sin_table_f32 = 32'h3F7E1323;
            10'd139: sin_table_f32 = 32'h3F7DABCC;
            10'd140: sin_table_f32 = 32'h3F7D3AAC;
            10'd141: sin_table_f32 = 32'h3F7CBFC9;
            10'd142: sin_table_f32 = 32'h3F7C3B28;
            10'd143: sin_table_f32 = 32'h3F7BACCD;
            10'd144: sin_table_f32 = 32'h3F7B14BE;
            10'd145: sin_table_f32 = 32'h3F7A7302;
            10'd146: sin_table_f32 = 32'h3F79C79D;
            10'd147: sin_table_f32 = 32'h3F791298;
            10'd148: sin_table_f32 = 32'h3F7853F8;
            10'd149: sin_table_f32 = 32'h3F778BC5;
            10'd150: sin_table_f32 = 32'h3F76BA07;
            10'd151: sin_table_f32 = 32'h3F75DEC6;
            10'd152: sin_table_f32 = 32'h3F74FA0B;
            10'd153: sin_table_f32 = 32'h3F740BDD;
            10'd154: sin_table_f32 = 32'h3F731447;
            10'd155: sin_table_f32 = 32'h3F721352;
            10'd156: sin_table_f32 = 32'h3F710908;
            10'd157: sin_table_f32 = 32'h3F6FF573;
            10'd158: sin_table_f32 = 32'h3F6ED89E;
            10'd159: sin_table_f32 = 32'h3F6DB293;
            10'd160: sin_table_f32 = 32'h3F6C835E;
            10'd161: sin_table_f32 = 32'h3F6B4B0C;
            10'd162: sin_table_f32 = 32'h3F6A09A7;
            10'd163: sin_table_f32 = 32'h3F68BF3C;
            10'd164: sin_table_f32 = 32'h3F676BD8;
            10'd165: sin_table_f32 = 32'h3F660F88;
            10'd166: sin_table_f32 = 32'h3F64AA59;
            10'd167: sin_table_f32 = 32'h3F633C5A;
            10'd168: sin_table_f32 = 32'h3F61C597;
            10'd169: sin_table_f32 = 32'h3F604621;
            10'd170: sin_table_f32 = 32'h3F5EBE05;
            10'd171: sin_table_f32 = 32'h3F5D2D53;
            10'd172: sin_table_f32 = 32'h3F5B941A;
            10'd173: sin_table_f32 = 32'h3F59F26A;
            10'd174: sin_table_f32 = 32'h3F584853;
            10'd175: sin_table_f32 = 32'h3F5695E5;
            10'd176: sin_table_f32 = 32'h3F54DB31;
            10'd177: sin_table_f32 = 32'h3F531849;
            10'd178: sin_table_f32 = 32'h3F514D3D;
            10'd179: sin_table_f32 = 32'h3F4F7A20;
            10'd180: sin_table_f32 = 32'h3F4D9F02;
            10'd181: sin_table_f32 = 32'h3F4BBBF8;
            10'd182: sin_table_f32 = 32'h3F49D112;
            10'd183: sin_table_f32 = 32'h3F47DE65;
            10'd184: sin_table_f32 = 32'h3F45E403;
            10'd185: sin_table_f32 = 32'h3F43E201;
            10'd186: sin_table_f32 = 32'h3F41D870;
            10'd187: sin_table_f32 = 32'h3F3FC767;
            10'd188: sin_table_f32 = 32'h3F3DAEF9;
            10'd189: sin_table_f32 = 32'h3F3B8F3B;
            10'd190: sin_table_f32 = 32'h3F396842;
            10'd191: sin_table_f32 = 32'h3F373A23;
            10'd192: sin_table_f32 = 32'h3F3504F3;
            10'd193: sin_table_f32 = 32'h3F32C8C9;
            10'd194: sin_table_f32 = 32'h3F3085BB;
            10'd195: sin_table_f32 = 32'h3F2E3BDE;
            10'd196: sin_table_f32 = 32'h3F2BEB4A;
            10'd197: sin_table_f32 = 32'h3F299415;
            10'd198: sin_table_f32 = 32'h3F273656;
            10'd199: sin_table_f32 = 32'h3F24D225;
            10'd200: sin_table_f32 = 32'h3F226799;
            10'd201: sin_table_f32 = 32'h3F1FF6CB;
            10'd202: sin_table_f32 = 32'h3F1D7FD1;
            10'd203: sin_table_f32 = 32'h3F1B02C6;
            10'd204: sin_table_f32 = 32'h3F187FC0;
            10'd205: sin_table_f32 = 32'h3F15F6D9;
            10'd206: sin_table_f32 = 32'h3F13682A;
            10'd207: sin_table_f32 = 32'h3F10D3CD;
            10'd208: sin_table_f32 = 32'h3F0E39DA;
            10'd209: sin_table_f32 = 32'h3F0B9A6B;
            10'd210: sin_table_f32 = 32'h3F08F59B;
            10'd211: sin_table_f32 = 32'h3F064B82;
            10'd212: sin_table_f32 = 32'h3F039C3D;
            10'd213: sin_table_f32 = 32'h3F00E7E4;
            10'd214: sin_table_f32 = 32'h3EFC5D27;
            10'd215: sin_table_f32 = 32'h3EF6E0CB;
            10'd216: sin_table_f32 = 32'h3EF15AEA;
            10'd217: sin_table_f32 = 32'h3EEBCBBB;
            10'd218: sin_table_f32 = 32'h3EE63375;
            10'd219: sin_table_f32 = 32'h3EE0924F;
            10'd220: sin_table_f32 = 32'h3EDAE880;
            10'd221: sin_table_f32 = 32'h3ED53641;
            10'd222: sin_table_f32 = 32'h3ECF7BCA;
            10'd223: sin_table_f32 = 32'h3EC9B953;
            10'd224: sin_table_f32 = 32'h3EC3EF15;
            10'd225: sin_table_f32 = 32'h3EBE1D49;
            10'd226: sin_table_f32 = 32'h3EB8442A;
            10'd227: sin_table_f32 = 32'h3EB263EF;
            10'd228: sin_table_f32 = 32'h3EAC7CD4;
            10'd229: sin_table_f32 = 32'h3EA68F12;
            10'd230: sin_table_f32 = 32'h3EA09AE5;
            10'd231: sin_table_f32 = 32'h3E9AA086;
            10'd232: sin_table_f32 = 32'h3E94A032;
            10'd233: sin_table_f32 = 32'h3E8E9A22;
            10'd234: sin_table_f32 = 32'h3E888E93;
            10'd235: sin_table_f32 = 32'h3E827DC0;
            10'd236: sin_table_f32 = 32'h3E78CFCC;
            10'd237: sin_table_f32 = 32'h3E6C9A7F;
            10'd238: sin_table_f32 = 32'h3E605C13;
            10'd239: sin_table_f32 = 32'h3E541501;
            10'd240: sin_table_f32 = 32'h3E47C5C2;
            10'd241: sin_table_f32 = 32'h3E3B6ECF;
            10'd242: sin_table_f32 = 32'h3E2F10A2;
            10'd243: sin_table_f32 = 32'h3E22ABB5;
            10'd244: sin_table_f32 = 32'h3E164083;
            10'd245: sin_table_f32 = 32'h3E09CF87;
            10'd246: sin_table_f32 = 32'h3DFAB273;
            10'd247: sin_table_f32 = 32'h3DE1BC2F;
            10'd248: sin_table_f32 = 32'h3DC8BD36;
            10'd249: sin_table_f32 = 32'h3DAFB680;
            10'd250: sin_table_f32 = 32'h3D96A904;
            10'd251: sin_table_f32 = 32'h3D7B2B75;
            10'd252: sin_table_f32 = 32'h3D48FB2E;
            10'd253: sin_table_f32 = 32'h3D16C32B;
            10'd254: sin_table_f32 = 32'h3CC90AB1;
            10'd255: sin_table_f32 = 32'h3C490E92;
            10'd256: sin_table_f32 = 32'h00000000;
            10'd257: sin_table_f32 = 32'hBC490E92;
            10'd258: sin_table_f32 = 32'hBCC90AB1;
            10'd259: sin_table_f32 = 32'hBD16C32B;
            10'd260: sin_table_f32 = 32'hBD48FB2E;
            10'd261: sin_table_f32 = 32'hBD7B2B75;
            10'd262: sin_table_f32 = 32'hBD96A904;
            10'd263: sin_table_f32 = 32'hBDAFB680;
            10'd264: sin_table_f32 = 32'hBDC8BD36;
            10'd265: sin_table_f32 = 32'hBDE1BC2F;
            10'd266: sin_table_f32 = 32'hBDFAB273;
            10'd267: sin_table_f32 = 32'hBE09CF87;
            10'd268: sin_table_f32 = 32'hBE164083;
            10'd269: sin_table_f32 = 32'hBE22ABB5;
            10'd270: sin_table_f32 = 32'hBE2F10A2;
            10'd271: sin_table_f32 = 32'hBE3B6ECF;
            10'd272: sin_table_f32 = 32'hBE47C5C2;
            10'd273: sin_table_f32 = 32'hBE541501;
            10'd274: sin_table_f32 = 32'hBE605C13;
            10'd275: sin_table_f32 = 32'hBE6C9A7F;
            10'd276: sin_table_f32 = 32'hBE78CFCC;
            10'd277: sin_table_f32 = 32'hBE827DC0;
            10'd278: sin_table_f32 = 32'hBE888E93;
            10'd279: sin_table_f32 = 32'hBE8E9A22;
            10'd280: sin_table_f32 = 32'hBE94A032;
            10'd281: sin_table_f32 = 32'hBE9AA086;
            10'd282: sin_table_f32 = 32'hBEA09AE5;
            10'd283: sin_table_f32 = 32'hBEA68F12;
            10'd284: sin_table_f32 = 32'hBEAC7CD4;
            10'd285: sin_table_f32 = 32'hBEB263EF;
            10'd286: sin_table_f32 = 32'hBEB8442A;
            10'd287: sin_table_f32 = 32'hBEBE1D49;
            10'd288: sin_table_f32 = 32'hBEC3EF15;
            10'd289: sin_table_f32 = 32'hBEC9B953;
            10'd290: sin_table_f32 = 32'hBECF7BCA;
            10'd291: sin_table_f32 = 32'hBED53641;
            10'd292: sin_table_f32 = 32'hBEDAE880;
            10'd293: sin_table_f32 = 32'hBEE0924F;
            10'd294: sin_table_f32 = 32'hBEE63375;
            10'd295: sin_table_f32 = 32'hBEEBCBBB;
            10'd296: sin_table_f32 = 32'hBEF15AEA;
            10'd297: sin_table_f32 = 32'hBEF6E0CB;
            10'd298: sin_table_f32 = 32'hBEFC5D27;
            10'd299: sin_table_f32 = 32'hBF00E7E4;
            10'd300: sin_table_f32 = 32'hBF039C3D;
            10'd301: sin_table_f32 = 32'hBF064B82;
            10'd302: sin_table_f32 = 32'hBF08F59B;
            10'd303: sin_table_f32 = 32'hBF0B9A6B;
            10'd304: sin_table_f32 = 32'hBF0E39DA;
            10'd305: sin_table_f32 = 32'hBF10D3CD;
            10'd306: sin_table_f32 = 32'hBF13682A;
            10'd307: sin_table_f32 = 32'hBF15F6D9;
            10'd308: sin_table_f32 = 32'hBF187FC0;
            10'd309: sin_table_f32 = 32'hBF1B02C6;
            10'd310: sin_table_f32 = 32'hBF1D7FD1;
            10'd311: sin_table_f32 = 32'hBF1FF6CB;
            10'd312: sin_table_f32 = 32'hBF226799;
            10'd313: sin_table_f32 = 32'hBF24D225;
            10'd314: sin_table_f32 = 32'hBF273656;
            10'd315: sin_table_f32 = 32'hBF299415;
            10'd316: sin_table_f32 = 32'hBF2BEB4A;
            10'd317: sin_table_f32 = 32'hBF2E3BDE;
            10'd318: sin_table_f32 = 32'hBF3085BB;
            10'd319: sin_table_f32 = 32'hBF32C8C9;
            10'd320: sin_table_f32 = 32'hBF3504F3;
            10'd321: sin_table_f32 = 32'hBF373A23;
            10'd322: sin_table_f32 = 32'hBF396842;
            10'd323: sin_table_f32 = 32'hBF3B8F3B;
            10'd324: sin_table_f32 = 32'hBF3DAEF9;
            10'd325: sin_table_f32 = 32'hBF3FC767;
            10'd326: sin_table_f32 = 32'hBF41D870;
            10'd327: sin_table_f32 = 32'hBF43E201;
            10'd328: sin_table_f32 = 32'hBF45E403;
            10'd329: sin_table_f32 = 32'hBF47DE65;
            10'd330: sin_table_f32 = 32'hBF49D112;
            10'd331: sin_table_f32 = 32'hBF4BBBF8;
            10'd332: sin_table_f32 = 32'hBF4D9F02;
            10'd333: sin_table_f32 = 32'hBF4F7A20;
            10'd334: sin_table_f32 = 32'hBF514D3D;
            10'd335: sin_table_f32 = 32'hBF531849;
            10'd336: sin_table_f32 = 32'hBF54DB31;
            10'd337: sin_table_f32 = 32'hBF5695E5;
            10'd338: sin_table_f32 = 32'hBF584853;
            10'd339: sin_table_f32 = 32'hBF59F26A;
            10'd340: sin_table_f32 = 32'hBF5B941A;
            10'd341: sin_table_f32 = 32'hBF5D2D53;
            10'd342: sin_table_f32 = 32'hBF5EBE05;
            10'd343: sin_table_f32 = 32'hBF604621;
            10'd344: sin_table_f32 = 32'hBF61C597;
            10'd345: sin_table_f32 = 32'hBF633C5A;
            10'd346: sin_table_f32 = 32'hBF64AA59;
            10'd347: sin_table_f32 = 32'hBF660F88;
            10'd348: sin_table_f32 = 32'hBF676BD8;
            10'd349: sin_table_f32 = 32'hBF68BF3C;
            10'd350: sin_table_f32 = 32'hBF6A09A7;
            10'd351: sin_table_f32 = 32'hBF6B4B0C;
            10'd352: sin_table_f32 = 32'hBF6C835E;
            10'd353: sin_table_f32 = 32'hBF6DB293;
            10'd354: sin_table_f32 = 32'hBF6ED89E;
            10'd355: sin_table_f32 = 32'hBF6FF573;
            10'd356: sin_table_f32 = 32'hBF710908;
            10'd357: sin_table_f32 = 32'hBF721352;
            10'd358: sin_table_f32 = 32'hBF731447;
            10'd359: sin_table_f32 = 32'hBF740BDD;
            10'd360: sin_table_f32 = 32'hBF74FA0B;
            10'd361: sin_table_f32 = 32'hBF75DEC6;
            10'd362: sin_table_f32 = 32'hBF76BA07;
            10'd363: sin_table_f32 = 32'hBF778BC5;
            10'd364: sin_table_f32 = 32'hBF7853F8;
            10'd365: sin_table_f32 = 32'hBF791298;
            10'd366: sin_table_f32 = 32'hBF79C79D;
            10'd367: sin_table_f32 = 32'hBF7A7302;
            10'd368: sin_table_f32 = 32'hBF7B14BE;
            10'd369: sin_table_f32 = 32'hBF7BACCD;
            10'd370: sin_table_f32 = 32'hBF7C3B28;
            10'd371: sin_table_f32 = 32'hBF7CBFC9;
            10'd372: sin_table_f32 = 32'hBF7D3AAC;
            10'd373: sin_table_f32 = 32'hBF7DABCC;
            10'd374: sin_table_f32 = 32'hBF7E1323;
            10'd375: sin_table_f32 = 32'hBF7E70B0;
            10'd376: sin_table_f32 = 32'hBF7EC46D;
            10'd377: sin_table_f32 = 32'hBF7F0E58;
            10'd378: sin_table_f32 = 32'hBF7F4E6D;
            10'd379: sin_table_f32 = 32'hBF7F84AB;
            10'd380: sin_table_f32 = 32'hBF7FB10F;
            10'd381: sin_table_f32 = 32'hBF7FD397;
            10'd382: sin_table_f32 = 32'hBF7FEC43;
            10'd383: sin_table_f32 = 32'hBF7FFB11;
            10'd384: sin_table_f32 = 32'hBF800000;
            10'd385: sin_table_f32 = 32'hBF7FFB11;
            10'd386: sin_table_f32 = 32'hBF7FEC43;
            10'd387: sin_table_f32 = 32'hBF7FD397;
            10'd388: sin_table_f32 = 32'hBF7FB10F;
            10'd389: sin_table_f32 = 32'hBF7F84AB;
            10'd390: sin_table_f32 = 32'hBF7F4E6D;
            10'd391: sin_table_f32 = 32'hBF7F0E58;
            10'd392: sin_table_f32 = 32'hBF7EC46D;
            10'd393: sin_table_f32 = 32'hBF7E70B0;
            10'd394: sin_table_f32 = 32'hBF7E1323;
            10'd395: sin_table_f32 = 32'hBF7DABCC;
            10'd396: sin_table_f32 = 32'hBF7D3AAC;
            10'd397: sin_table_f32 = 32'hBF7CBFC9;
            10'd398: sin_table_f32 = 32'hBF7C3B28;
            10'd399: sin_table_f32 = 32'hBF7BACCD;
            10'd400: sin_table_f32 = 32'hBF7B14BE;
            10'd401: sin_table_f32 = 32'hBF7A7302;
            10'd402: sin_table_f32 = 32'hBF79C79D;
            10'd403: sin_table_f32 = 32'hBF791298;
            10'd404: sin_table_f32 = 32'hBF7853F8;
            10'd405: sin_table_f32 = 32'hBF778BC5;
            10'd406: sin_table_f32 = 32'hBF76BA07;
            10'd407: sin_table_f32 = 32'hBF75DEC6;
            10'd408: sin_table_f32 = 32'hBF74FA0B;
            10'd409: sin_table_f32 = 32'hBF740BDD;
            10'd410: sin_table_f32 = 32'hBF731447;
            10'd411: sin_table_f32 = 32'hBF721352;
            10'd412: sin_table_f32 = 32'hBF710908;
            10'd413: sin_table_f32 = 32'hBF6FF573;
            10'd414: sin_table_f32 = 32'hBF6ED89E;
            10'd415: sin_table_f32 = 32'hBF6DB293;
            10'd416: sin_table_f32 = 32'hBF6C835E;
            10'd417: sin_table_f32 = 32'hBF6B4B0C;
            10'd418: sin_table_f32 = 32'hBF6A09A7;
            10'd419: sin_table_f32 = 32'hBF68BF3C;
            10'd420: sin_table_f32 = 32'hBF676BD8;
            10'd421: sin_table_f32 = 32'hBF660F88;
            10'd422: sin_table_f32 = 32'hBF64AA59;
            10'd423: sin_table_f32 = 32'hBF633C5A;
            10'd424: sin_table_f32 = 32'hBF61C597;
            10'd425: sin_table_f32 = 32'hBF604621;
            10'd426: sin_table_f32 = 32'hBF5EBE05;
            10'd427: sin_table_f32 = 32'hBF5D2D53;
            10'd428: sin_table_f32 = 32'hBF5B941A;
            10'd429: sin_table_f32 = 32'hBF59F26A;
            10'd430: sin_table_f32 = 32'hBF584853;
            10'd431: sin_table_f32 = 32'hBF5695E5;
            10'd432: sin_table_f32 = 32'hBF54DB31;
            10'd433: sin_table_f32 = 32'hBF531849;
            10'd434: sin_table_f32 = 32'hBF514D3D;
            10'd435: sin_table_f32 = 32'hBF4F7A20;
            10'd436: sin_table_f32 = 32'hBF4D9F02;
            10'd437: sin_table_f32 = 32'hBF4BBBF8;
            10'd438: sin_table_f32 = 32'hBF49D112;
            10'd439: sin_table_f32 = 32'hBF47DE65;
            10'd440: sin_table_f32 = 32'hBF45E403;
            10'd441: sin_table_f32 = 32'hBF43E201;
            10'd442: sin_table_f32 = 32'hBF41D870;
            10'd443: sin_table_f32 = 32'hBF3FC767;
            10'd444: sin_table_f32 = 32'hBF3DAEF9;
            10'd445: sin_table_f32 = 32'hBF3B8F3B;
            10'd446: sin_table_f32 = 32'hBF396842;
            10'd447: sin_table_f32 = 32'hBF373A23;
            10'd448: sin_table_f32 = 32'hBF3504F3;
            10'd449: sin_table_f32 = 32'hBF32C8C9;
            10'd450: sin_table_f32 = 32'hBF3085BB;
            10'd451: sin_table_f32 = 32'hBF2E3BDE;
            10'd452: sin_table_f32 = 32'hBF2BEB4A;
            10'd453: sin_table_f32 = 32'hBF299415;
            10'd454: sin_table_f32 = 32'hBF273656;
            10'd455: sin_table_f32 = 32'hBF24D225;
            10'd456: sin_table_f32 = 32'hBF226799;
            10'd457: sin_table_f32 = 32'hBF1FF6CB;
            10'd458: sin_table_f32 = 32'hBF1D7FD1;
            10'd459: sin_table_f32 = 32'hBF1B02C6;
            10'd460: sin_table_f32 = 32'hBF187FC0;
            10'd461: sin_table_f32 = 32'hBF15F6D9;
            10'd462: sin_table_f32 = 32'hBF13682A;
            10'd463: sin_table_f32 = 32'hBF10D3CD;
            10'd464: sin_table_f32 = 32'hBF0E39DA;
            10'd465: sin_table_f32 = 32'hBF0B9A6B;
            10'd466: sin_table_f32 = 32'hBF08F59B;
            10'd467: sin_table_f32 = 32'hBF064B82;
            10'd468: sin_table_f32 = 32'hBF039C3D;
            10'd469: sin_table_f32 = 32'hBF00E7E4;
            10'd470: sin_table_f32 = 32'hBEFC5D27;
            10'd471: sin_table_f32 = 32'hBEF6E0CB;
            10'd472: sin_table_f32 = 32'hBEF15AEA;
            10'd473: sin_table_f32 = 32'hBEEBCBBB;
            10'd474: sin_table_f32 = 32'hBEE63375;
            10'd475: sin_table_f32 = 32'hBEE0924F;
            10'd476: sin_table_f32 = 32'hBEDAE880;
            10'd477: sin_table_f32 = 32'hBED53641;
            10'd478: sin_table_f32 = 32'hBECF7BCA;
            10'd479: sin_table_f32 = 32'hBEC9B953;
            10'd480: sin_table_f32 = 32'hBEC3EF15;
            10'd481: sin_table_f32 = 32'hBEBE1D49;
            10'd482: sin_table_f32 = 32'hBEB8442A;
            10'd483: sin_table_f32 = 32'hBEB263EF;
            10'd484: sin_table_f32 = 32'hBEAC7CD4;
            10'd485: sin_table_f32 = 32'hBEA68F12;
            10'd486: sin_table_f32 = 32'hBEA09AE5;
            10'd487: sin_table_f32 = 32'hBE9AA086;
            10'd488: sin_table_f32 = 32'hBE94A032;
            10'd489: sin_table_f32 = 32'hBE8E9A22;
            10'd490: sin_table_f32 = 32'hBE888E93;
            10'd491: sin_table_f32 = 32'hBE827DC0;
            10'd492: sin_table_f32 = 32'hBE78CFCC;
            10'd493: sin_table_f32 = 32'hBE6C9A7F;
            10'd494: sin_table_f32 = 32'hBE605C13;
            10'd495: sin_table_f32 = 32'hBE541501;
            10'd496: sin_table_f32 = 32'hBE47C5C2;
            10'd497: sin_table_f32 = 32'hBE3B6ECF;
            10'd498: sin_table_f32 = 32'hBE2F10A2;
            10'd499: sin_table_f32 = 32'hBE22ABB5;
            10'd500: sin_table_f32 = 32'hBE164083;
            10'd501: sin_table_f32 = 32'hBE09CF87;
            10'd502: sin_table_f32 = 32'hBDFAB273;
            10'd503: sin_table_f32 = 32'hBDE1BC2F;
            10'd504: sin_table_f32 = 32'hBDC8BD36;
            10'd505: sin_table_f32 = 32'hBDAFB680;
            10'd506: sin_table_f32 = 32'hBD96A904;
            10'd507: sin_table_f32 = 32'hBD7B2B75;
            10'd508: sin_table_f32 = 32'hBD48FB2E;
            10'd509: sin_table_f32 = 32'hBD16C32B;
            10'd510: sin_table_f32 = 32'hBCC90AB1;
            10'd511: sin_table_f32 = 32'hBC490E92;
            10'd512: sin_table_f32 = 32'h80000000;
            default: sin_table_f32 = 32'h00000000;
        endcase
    end
endfunction

logic [31:0] calc_sin;
logic [31:0] calc_cos;

always_comb begin
    logic [31:0] theta;
    logic [31:0] in_v;
    logic [31:0] findex;
    logic [31:0] fract;
    logic [31:0] int_part_bits;
    logic [31:0] indexS_fp;
    logic [31:0] indexS_bits;
    logic [8:0] indexS;
    logic [8:0] indexC;
    logic [9:0] indexC_p1;
    logic [9:0] indexS_p1;
    logic [31:0] f1;
    logic [31:0] f2;
    logic [31:0] d1;
    logic [31:0] d2;
    logic [31:0] Df;
    logic [31:0] temp;
    logic [31:0] sin_val;
    logic [31:0] cos_val;

    theta = in_theta;
    calc_sin = 32'd0;
    calc_cos = 32'd0;
    in_v = 32'd0;
    findex = 32'd0;
    fract = 32'd0;
    int_part_bits = 32'd0;
    indexS_fp = 32'd0;
    indexS_bits = 32'd0;
    indexS = 9'd0;
    indexC = 9'd0;
    indexC_p1 = 10'd0;
    indexS_p1 = 10'd0;
    f1 = 32'd0;
    f2 = 32'd0;
    d1 = 32'd0;
    d2 = 32'd0;
    Df = 32'd0;
    temp = 32'd0;
    sin_val = 32'd0;
    cos_val = 32'd0;

    if (fp32_is_nan(theta) || fp32_is_inf(theta)) begin
        calc_sin = CANON_NAN;
        calc_cos = CANON_NAN;
    end else begin
        in_v = fp32_mul_bits(theta, INV_360);
        if (fp32_cmp(in_v, ZERO) < 0)
            in_v = fp32_neg(in_v);

        // The rounded binary32 turn count is integral at magnitude 2**23.
        if (in_v[30:0] >= 31'h4B000000) begin
            in_v = ZERO;
        end else begin
            int_part_bits = trunc_to_u32_bits(in_v);
            in_v = fp32_sub_bits(in_v, u32_to_fp32_bits(int_part_bits));
        end

        findex = fp32_mul_bits(TABLE_SIZE_FP32, in_v);
        indexS_bits = trunc_to_u32_bits(findex);
        indexS = indexS_bits[8:0] & 9'h1FF;
        indexC = (indexS + 9'd128) & 9'h1FF;
        indexC_p1 = {1'b0, indexC} + 10'd1;
        indexS_p1 = {1'b0, indexS} + 10'd1;
        indexS_fp = u32_to_fp32_bits({23'd0, indexS});
        fract = fp32_sub_bits(findex, indexS_fp);

        f1 = sin_table_f32({1'b0, indexC});
        f2 = sin_table_f32(indexC_p1);
        d1 = fp32_neg(sin_table_f32({1'b0, indexS}));
        d2 = fp32_neg(sin_table_f32(indexS_p1));
        Df = fp32_sub_bits(f2, f1);
        temp = fp32_sub_bits(fp32_mul_bits(DN, fp32_add_bits(d1, d2)), fp32_mul_bits(TWO, Df));
        temp = fp32_add_bits(fp32_mul_bits(fract, temp), fp32_sub_bits(fp32_mul_bits(THREE, Df), fp32_mul_bits(fp32_add_bits(d2, fp32_mul_bits(TWO, d1)), DN)));
        temp = fp32_add_bits(fp32_mul_bits(fract, temp), fp32_mul_bits(d1, DN));
        cos_val = fp32_add_bits(fp32_mul_bits(fract, temp), f1);

        f1 = sin_table_f32({1'b0, indexS});
        f2 = sin_table_f32(indexS_p1);
        d1 = sin_table_f32({1'b0, indexC});
        d2 = sin_table_f32(indexC_p1);
        Df = fp32_sub_bits(f2, f1);
        temp = fp32_sub_bits(fp32_mul_bits(DN, fp32_add_bits(d1, d2)), fp32_mul_bits(TWO, Df));
        temp = fp32_add_bits(fp32_mul_bits(fract, temp), fp32_sub_bits(fp32_mul_bits(THREE, Df), fp32_mul_bits(fp32_add_bits(d2, fp32_mul_bits(TWO, d1)), DN)));
        temp = fp32_add_bits(fp32_mul_bits(fract, temp), fp32_mul_bits(d1, DN));
        sin_val = fp32_add_bits(fp32_mul_bits(fract, temp), f1);

        if (fp32_cmp(theta, ZERO) < 0)
            sin_val = fp32_neg(sin_val);

        calc_sin = sin_val;
        calc_cos = cos_val;
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy    <= 1'b0;
        out_sin <= 32'h00000000;
        out_cos <= 32'h00000000;
    end else if (input_accepted) begin
        busy    <= 1'b1;
        out_sin <= calc_sin;
        out_cos <= calc_cos;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
