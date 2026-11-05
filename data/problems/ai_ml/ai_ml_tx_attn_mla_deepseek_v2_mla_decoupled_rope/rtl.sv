/* verilator lint_off WIDTH */
/* verilator lint_off WIDTHEXPAND */
/* verilator lint_off WIDTHTRUNC */
/* verilator lint_off SELRANGE */
module ai_ml_tx_attn_mla_deepseek_v2_mla_decoupled_rope (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_position_id,
    input  logic [31:0]  in_rope_theta_bits,

    input  logic [31:0]  in_q_pe_0,
    input  logic [31:0]  in_q_pe_1,
    input  logic [31:0]  in_q_pe_2,
    input  logic [31:0]  in_q_pe_3,
    input  logic [31:0]  in_q_pe_4,
    input  logic [31:0]  in_q_pe_5,
    input  logic [31:0]  in_q_pe_6,
    input  logic [31:0]  in_q_pe_7,
    input  logic [31:0]  in_q_pe_8,
    input  logic [31:0]  in_q_pe_9,
    input  logic [31:0]  in_q_pe_10,
    input  logic [31:0]  in_q_pe_11,
    input  logic [31:0]  in_q_pe_12,
    input  logic [31:0]  in_q_pe_13,
    input  logic [31:0]  in_q_pe_14,
    input  logic [31:0]  in_q_pe_15,

    input  logic [31:0]  in_k_pe_0,
    input  logic [31:0]  in_k_pe_1,
    input  logic [31:0]  in_k_pe_2,
    input  logic [31:0]  in_k_pe_3,
    input  logic [31:0]  in_k_pe_4,
    input  logic [31:0]  in_k_pe_5,
    input  logic [31:0]  in_k_pe_6,
    input  logic [31:0]  in_k_pe_7,
    input  logic [31:0]  in_k_pe_8,
    input  logic [31:0]  in_k_pe_9,
    input  logic [31:0]  in_k_pe_10,
    input  logic [31:0]  in_k_pe_11,
    input  logic [31:0]  in_k_pe_12,
    input  logic [31:0]  in_k_pe_13,
    input  logic [31:0]  in_k_pe_14,
    input  logic [31:0]  in_k_pe_15,

    output logic         out_valid,
    input  logic         out_ready,

    output logic [31:0]  out_q_pe_rot_0,
    output logic [31:0]  out_q_pe_rot_1,
    output logic [31:0]  out_q_pe_rot_2,
    output logic [31:0]  out_q_pe_rot_3,
    output logic [31:0]  out_q_pe_rot_4,
    output logic [31:0]  out_q_pe_rot_5,
    output logic [31:0]  out_q_pe_rot_6,
    output logic [31:0]  out_q_pe_rot_7,
    output logic [31:0]  out_q_pe_rot_8,
    output logic [31:0]  out_q_pe_rot_9,
    output logic [31:0]  out_q_pe_rot_10,
    output logic [31:0]  out_q_pe_rot_11,
    output logic [31:0]  out_q_pe_rot_12,
    output logic [31:0]  out_q_pe_rot_13,
    output logic [31:0]  out_q_pe_rot_14,
    output logic [31:0]  out_q_pe_rot_15,

    output logic [31:0]  out_k_pe_rot_0,
    output logic [31:0]  out_k_pe_rot_1,
    output logic [31:0]  out_k_pe_rot_2,
    output logic [31:0]  out_k_pe_rot_3,
    output logic [31:0]  out_k_pe_rot_4,
    output logic [31:0]  out_k_pe_rot_5,
    output logic [31:0]  out_k_pe_rot_6,
    output logic [31:0]  out_k_pe_rot_7,
    output logic [31:0]  out_k_pe_rot_8,
    output logic [31:0]  out_k_pe_rot_9,
    output logic [31:0]  out_k_pe_rot_10,
    output logic [31:0]  out_k_pe_rot_11,
    output logic [31:0]  out_k_pe_rot_12,
    output logic [31:0]  out_k_pe_rot_13,
    output logic [31:0]  out_k_pe_rot_14,
    output logic [31:0]  out_k_pe_rot_15
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

    localparam logic [31:0] FP32_CANON_QNAN = 32'h7FC00000;
    localparam logic [31:0] THETA_BITS = 32'h461C4000; // 10000.0

    function automatic logic [31:0] fp32_neg(input logic [31:0] x);
    fp32_neg = x ^ 32'h80000000;
endfunction

    // ----- LUT (16 positions × 8 pairs) for theta=10000.0, D=16 -----
    function automatic logic [31:0] cos_lut(input logic [3:0] p, input logic [2:0] m);
        case ({p, m})
            // p=0
            7'd0:   cos_lut = 32'h3F800000; 7'd1:   cos_lut = 32'h3F800000;
            7'd2:   cos_lut = 32'h3F800000; 7'd3:   cos_lut = 32'h3F800000;
            7'd4:   cos_lut = 32'h3F800000; 7'd5:   cos_lut = 32'h3F800000;
            7'd6:   cos_lut = 32'h3F800000; 7'd7:   cos_lut = 32'h3F800000;
            // p=1
            7'd8:   cos_lut = 32'h3F0A5140; 7'd9:   cos_lut = 32'h3F734E6A;
            7'd10:  cos_lut = 32'h3F7EB898; 7'd11:  cos_lut = 32'h3F7FDF3C;
            7'd12:  cos_lut = 32'h3F7FFCB9; 7'd13:  cos_lut = 32'h3F7FFFAC;
            7'd14:  cos_lut = 32'h3F7FFFF8; 7'd15:  cos_lut = 32'h3F7FFFFF;
            // p=2
            7'd16:  cos_lut = 32'hBED51133; 7'd17:  cos_lut = 32'h3F4E7BEC;
            7'd18:  cos_lut = 32'h3F7AE5A5; 7'd19:  cos_lut = 32'h3F7F7CF9;
            7'd20:  cos_lut = 32'h3F7FF2E5; 7'd21:  cos_lut = 32'h3F7FFEB0;
            7'd22:  cos_lut = 32'h3F7FFFDE; 7'd23:  cos_lut = 32'h3F7FFFFD;
            // p=3
            7'd24:  cos_lut = 32'hBF7D7026; 7'd25:  cos_lut = 32'h3F152F57;
            7'd26:  cos_lut = 32'h3F7490EF; 7'd27:  cos_lut = 32'h3F7ED94F;
            7'd28:  cos_lut = 32'h3F7FE283; 7'd29:  cos_lut = 32'h3F7FFD0D;
            7'd30:  cos_lut = 32'h3F7FFFB5; 7'd31:  cos_lut = 32'h3F7FFFF8;
            // p=4
            7'd32:  cos_lut = 32'hBF275530; 7'd33:  cos_lut = 32'h3E9A2EB1;
            7'd34:  cos_lut = 32'h3F6BCAA7; 7'd35:  cos_lut = 32'h3F7DF469;
            7'd36:  cos_lut = 32'h3F7FCB94; 7'd37:  cos_lut = 32'h3F7FFAC2;
            7'd38:  cos_lut = 32'h3F7FFF7A; 7'd39:  cos_lut = 32'h3F7FFFF3;
            // p=5
            7'd40:  cos_lut = 32'h3E913C2C; 7'd41:  cos_lut = 32'hBC2972D4;
            7'd42:  cos_lut = 32'h3F60A940; 7'd43:  cos_lut = 32'h3F7CCE81;
            7'd44:  cos_lut = 32'h3F7FAE19; 7'd45:  cos_lut = 32'h3F7FF7CF;
            7'd46:  cos_lut = 32'h3F7FFF2E; 7'd47:  cos_lut = 32'h3F7FFFEB;
            // p=6
            7'd48:  cos_lut = 32'h3F75CDB8; 7'd49:  cos_lut = 32'hBEA43F6F;
            7'd50:  cos_lut = 32'h3F534932; 7'd51:  cos_lut = 32'h3F7B67E3;
            7'd52:  cos_lut = 32'h3F7F8A12; 7'd53:  cos_lut = 32'h3F7FF434;
            7'd54:  cos_lut = 32'h3F7FFED2; 7'd55:  cos_lut = 32'h3F7FFFE2;
            // p=7
            7'd56:  cos_lut = 32'h3F40FFBD; 7'd57:  cos_lut = 32'hBF1974BB;
            7'd58:  cos_lut = 32'h3F43CCB3; 7'd59:  cos_lut = 32'h3F79C0EA;
            7'd60:  cos_lut = 32'h3F7F5F81; 7'd61:  cos_lut = 32'h3F7FEFF2;
            7'd62:  cos_lut = 32'h3F7FFE65; 7'd63:  cos_lut = 32'h3F7FFFD7;
            // p=8
            7'd64:  cos_lut = 32'hBE14FDF6; 7'd65:  cos_lut = 32'hBF5191E6;
            7'd66:  cos_lut = 32'h3F325B5F; 7'd67:  cos_lut = 32'h3F77DA02;
            7'd68:  cos_lut = 32'h3F7F2E66; 7'd69:  cos_lut = 32'h3F7FEB08;
            7'd70:  cos_lut = 32'h3F7FFDE7; 7'd71:  cos_lut = 32'h3F7FFFCA;
            // p=9
            7'd72:  cos_lut = 32'hBF693FD5; 7'd73:  cos_lut = 32'hBF74E6A2;
            7'd74:  cos_lut = 32'h3F1F21D5; 7'd75:  cos_lut = 32'h3F75B3A9;
            7'd76:  cos_lut = 32'h3F7EF6C2; 7'd77:  cos_lut = 32'h3F7FE576;
            7'd78:  cos_lut = 32'h3F7FFD59; 7'd79:  cos_lut = 32'h3F7FFFBC;
            // p=10
            7'd80:  cos_lut = 32'hBF56CD64; 7'd81:  cos_lut = 32'hBF7FF1FB;
            7'd82:  cos_lut = 32'h3F0A5140; 7'd83:  cos_lut = 32'h3F734E6A;
            7'd84:  cos_lut = 32'h3F7EB898; 7'd85:  cos_lut = 32'h3F7FDF3C;
            7'd86:  cos_lut = 32'h3F7FFCB9; 7'd87:  cos_lut = 32'h3F7FFFAC;
            // p=11
            7'd88:  cos_lut = 32'h3B910572; 7'd89:  cos_lut = 32'hBF719B8C;
            7'd90:  cos_lut = 32'h3EE83DC0; 7'd91:  cos_lut = 32'h3F70AAE4;
            7'd92:  cos_lut = 32'h3F7E73E8; 7'd93:  cos_lut = 32'h3F7FD85B;
            7'd94:  cos_lut = 32'h3F7FFC09; 7'd95:  cos_lut = 32'h3F7FFF9A;
            // p=12
            7'd96:  cos_lut = 32'h3F5806D0; 7'd97:  cos_lut = 32'hBF4B4F55;
            7'd98:  cos_lut = 32'h3EB986F5; 7'd99:  cos_lut = 32'h3F6DC9C3;
            7'd100: cos_lut = 32'h3F7E28B5; 7'd101: cos_lut = 32'h3F7FD0D2;
            7'd102: cos_lut = 32'h3F7FFB48; 7'd103: cos_lut = 32'h3F7FFF87;
            // p=13
            7'd104: cos_lut = 32'h3F684E6F; 7'd105: cos_lut = 32'hBF10D99D;
            7'd106: cos_lut = 32'h3E88F59B; 7'd107: cos_lut = 32'h3F6AABC3;
            7'd108: cos_lut = 32'h3F7DD700; 7'd109: cos_lut = 32'h3F7FC8A1;
            7'd110: cos_lut = 32'h3F7FFA76; 7'd111: cos_lut = 32'h3F7FFF72;
            // p=14
            7'd112: cos_lut = 32'h3E0C04D7; 7'd113: cos_lut = 32'hBE900D0F;
            7'd114: cos_lut = 32'h3E2E0BDE; 7'd115: cos_lut = 32'h3F6751B1;
            7'd116: cos_lut = 32'h3F7D7ECC; 7'd117: cos_lut = 32'h3F7FBFC9;
            7'd118: cos_lut = 32'h3F7FF994; 7'd119: cos_lut = 32'h3F7FFF5C;
            // p=15
            7'd120: cos_lut = 32'hBF427AE8; 7'd121: cos_lut = 32'h3CFE22F7;
            7'd122: cos_lut = 32'h3D90DEAA; 7'd123: cos_lut = 32'h3F63BC68;
            7'd124: cos_lut = 32'h3F7D201A; 7'd125: cos_lut = 32'h3F7FB649;
            7'd126: cos_lut = 32'h3F7FF8A1; 7'd127: cos_lut = 32'h3F7FFF43;
            default: cos_lut = 32'd0;
        endcase
    endfunction

    function automatic logic [31:0] sin_lut(input logic [3:0] p, input logic [2:0] m);
        case ({p, m})
            // p=0
            7'd0:   sin_lut = 32'h00000000; 7'd1:   sin_lut = 32'h00000000;
            7'd2:   sin_lut = 32'h00000000; 7'd3:   sin_lut = 32'h00000000;
            7'd4:   sin_lut = 32'h00000000; 7'd5:   sin_lut = 32'h00000000;
            7'd6:   sin_lut = 32'h00000000; 7'd7:   sin_lut = 32'h00000000;
            // p=1
            7'd8:   sin_lut = 32'h3F576AA4; 7'd9:   sin_lut = 32'h3E9F393E;
            7'd10:  sin_lut = 32'h3DCC7576; 7'd11:  sin_lut = 32'h3D01815C;
            7'd12:  sin_lut = 32'h3C23D657; 7'd13:  sin_lut = 32'h3B4F3E20;
            7'd14:  sin_lut = 32'h3A83126D; 7'd15:  sin_lut = 32'h39A5CB5F;
            // p=2
            7'd16:  sin_lut = 32'h3F68C7B7; 7'd17:  sin_lut = 32'h3F17541B;
            7'd18:  sin_lut = 32'h3E4B6FF9; 7'd19:  sin_lut = 32'h3D8170C8;
            7'd20:  sin_lut = 32'h3CA3D43E; 7'd21:  sin_lut = 32'h3BCF3DDD;
            7'd22:  sin_lut = 32'h3B031269; 7'd23:  sin_lut = 32'h3A25CB5F;
            // p=3
            7'd24:  sin_lut = 32'h3E1081C3; 7'd25:  sin_lut = 32'h3F5009C2;
            7'd26:  sin_lut = 32'h3E974E6D; 7'd27:  sin_lut = 32'h3DC1FFC1;
            7'd28:  sin_lut = 32'h3CF5B920; 7'd29:  sin_lut = 32'h3C1B6E11;
            7'd30:  sin_lut = 32'h3B449B93; 7'd31:  sin_lut = 32'h3A78B10D;
            // p=4
            7'd32:  sin_lut = 32'hBF41BDCF; 7'd33:  sin_lut = 32'h3F741DDE;
            7'd34:  sin_lut = 32'h3EC761D7; 7'd35:  sin_lut = 32'h3E012E88;
            7'd36:  sin_lut = 32'h3D23CBDB; 7'd37:  sin_lut = 32'h3C4F3CCD;
            7'd38:  sin_lut = 32'h3B831258; 7'd39:  sin_lut = 32'h3AA5CB5C;
            // p=5
            7'd40:  sin_lut = 32'hBF757C10; 7'd41:  sin_lut = 32'h3F7FFC7F;
            7'd42:  sin_lut = 32'h3EF57744; 7'd43:  sin_lut = 32'h3E213C1E;
            7'd44:  sin_lut = 32'h3D4CB6F5; 7'd45:  sin_lut = 32'h3C818581;
            7'd46:  sin_lut = 32'h3BA3D6DE; 7'd47:  sin_lut = 32'h3ACF3E31;
            // p=6
            7'd48:  sin_lut = 32'hBE8F0F8C; 7'd49:  sin_lut = 32'h3F72784D;
            7'd50:  sin_lut = 32'h3F108C69; 7'd51:  sin_lut = 32'h3E41206F;
            7'd52:  sin_lut = 32'h3D759CD1; 7'd53:  sin_lut = 32'h3C9B6C46;
            7'd54:  sin_lut = 32'h3BC49B59; 7'd55:  sin_lut = 32'h3AF8B105;
            // p=7
            7'd56:  sin_lut = 32'h3F283046; 7'd57:  sin_lut = 32'h3F4CE86F;
            7'd58:  sin_lut = 32'h3F24EB73; 7'd59:  sin_lut = 32'h3E60D350;
            7'd60:  sin_lut = 32'h3D8F3E32; 7'd61:  sin_lut = 32'h3CB552A6;
            7'd62:  sin_lut = 32'h3BE55FC7; 7'd63:  sin_lut = 32'h3B1111EC;
            // p=8
            7'd64:  sin_lut = 32'h3F7D4695; 7'd65:  sin_lut = 32'h3F13067D;
            7'd66:  sin_lut = 32'h3F37A4A6; 7'd67:  sin_lut = 32'h3E802652;
            7'd68:  sin_lut = 32'h3DA3AA51; 7'd69:  sin_lut = 32'h3CCF388E;
            7'd70:  sin_lut = 32'h3C031213; 7'd71:  sin_lut = 32'h3B25CB54;
            // p=9
            7'd72:  sin_lut = 32'h3ED30132; 7'd73:  sin_lut = 32'h3E951FEA;
            7'd74:  sin_lut = 32'h3F48881D; 7'd75:  sin_lut = 32'h3E8FC22E;
            7'd76:  sin_lut = 32'h3DB8123F; 7'd77:  sin_lut = 32'h3CE91DEF;
            7'd78:  sin_lut = 32'h3C13743A; 7'd79:  sin_lut = 32'h3B3A84BB;
            // p=10
            7'd80:  sin_lut = 32'hBF0B44F8; 7'd81:  sin_lut = 32'hBCA97082;
            7'd82:  sin_lut = 32'h3F576AA4; 7'd83:  sin_lut = 32'h3E9F393E;
            7'd84:  sin_lut = 32'h3DCC7576; 7'd85:  sin_lut = 32'h3D01815C;
            7'd86:  sin_lut = 32'h3C23D657; 7'd87:  sin_lut = 32'h3B4F3E20;
            // p=11
            7'd88:  sin_lut = 32'hBF7FFF5C; 7'd89:  sin_lut = 32'hBEA94121;
            7'd90:  sin_lut = 32'h3F64262A; 7'd91:  sin_lut = 32'h3EAE878B;
            7'd92:  sin_lut = 32'h3DE0D372; 7'd93:  sin_lut = 32'h3D0E736B;
            7'd94:  sin_lut = 32'h3C34386A; 7'd95:  sin_lut = 32'h3B63F785;
            // p=12
            7'd96:  sin_lut = 32'hBF095CD8; 7'd97:  sin_lut = 32'hBF1B9126;
            7'd98:  sin_lut = 32'h3F6E9A1D; 7'd99:  sin_lut = 32'h3EBDA92C;
            7'd100: sin_lut = 32'h3DF52BAC; 7'd101: sin_lut = 32'h3D1B651D;
            7'd102: sin_lut = 32'h3C449A71; 7'd103: sin_lut = 32'h3B78B0E8;
            // p=13
            7'd104: sin_lut = 32'h3ED72022; 7'd105: sin_lut = 32'hBF53144C;
            7'd106: sin_lut = 32'h3F76ABC0; 7'd107: sin_lut = 32'h3ECC9A40;
            7'd108: sin_lut = 32'h3E04BED0; 7'd109: sin_lut = 32'h3D285669;
            7'd110: sin_lut = 32'h3C54FC6B; 7'd111: sin_lut = 32'h3B86B525;
            // p=14
            7'd112: sin_lut = 32'h3F7D9872; 7'd113: sin_lut = 32'hBF75A8B2;
            7'd114: sin_lut = 32'h3F7C466F; 7'd115: sin_lut = 32'h3EDB56F5;
            7'd116: sin_lut = 32'h3E0EE464; 7'd117: sin_lut = 32'h3D354746;
            7'd118: sin_lut = 32'h3C655E56; 7'd119: sin_lut = 32'h3B9111D4;
            // p=15
            7'd120: sin_lut = 32'h3F267944; 7'd121: sin_lut = 32'hBF7FE075;
            7'd122: sin_lut = 32'h3F7F5BD5; 7'd123: sin_lut = 32'h3EE9DB83;
            7'd124: sin_lut = 32'h3E19064F; 7'd125: sin_lut = 32'h3D4237AD;
            7'd126: sin_lut = 32'h3C75C033; 7'd127: sin_lut = 32'h3B9B6E83;
            default: sin_lut = 32'd0;
        endcase
    endfunction

    // ----- Datapath -----
    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy;

    // Combinational compute of next outputs.
    logic [31:0] q_in [0:15];
    logic [31:0] k_in [0:15];
    logic [31:0] q_out_next [0:15];
    logic [31:0] k_out_next [0:15];

    integer pi;
    always_comb begin
        logic [3:0] pos4;
        logic in_domain;
        logic [31:0] c, s;
        logic [31:0] qr, qi, kr, ki;
        logic [31:0] q_re, q_im, k_re, k_im;
        logic [31:0] qrc, qis, qrs, qic;
        logic [31:0] krc, kis, krs, kic;

        q_in[0]  = in_q_pe_0;  q_in[1]  = in_q_pe_1;
        q_in[2]  = in_q_pe_2;  q_in[3]  = in_q_pe_3;
        q_in[4]  = in_q_pe_4;  q_in[5]  = in_q_pe_5;
        q_in[6]  = in_q_pe_6;  q_in[7]  = in_q_pe_7;
        q_in[8]  = in_q_pe_8;  q_in[9]  = in_q_pe_9;
        q_in[10] = in_q_pe_10; q_in[11] = in_q_pe_11;
        q_in[12] = in_q_pe_12; q_in[13] = in_q_pe_13;
        q_in[14] = in_q_pe_14; q_in[15] = in_q_pe_15;

        k_in[0]  = in_k_pe_0;  k_in[1]  = in_k_pe_1;
        k_in[2]  = in_k_pe_2;  k_in[3]  = in_k_pe_3;
        k_in[4]  = in_k_pe_4;  k_in[5]  = in_k_pe_5;
        k_in[6]  = in_k_pe_6;  k_in[7]  = in_k_pe_7;
        k_in[8]  = in_k_pe_8;  k_in[9]  = in_k_pe_9;
        k_in[10] = in_k_pe_10; k_in[11] = in_k_pe_11;
        k_in[12] = in_k_pe_12; k_in[13] = in_k_pe_13;
        k_in[14] = in_k_pe_14; k_in[15] = in_k_pe_15;

        for (pi = 0; pi < 16; pi = pi + 1) begin
            q_out_next[pi] = 32'd0;
            k_out_next[pi] = 32'd0;
        end

        in_domain = (in_rope_theta_bits == THETA_BITS) && (in_position_id <= 32'd15);
        if (in_domain) begin
            pos4 = in_position_id[3:0];
            for (pi = 0; pi < 8; pi = pi + 1) begin
                c  = cos_lut(pos4, pi[2:0]);
                s  = sin_lut(pos4, pi[2:0]);
                qr = q_in[2*pi];
                qi = q_in[2*pi + 1];
                kr = k_in[2*pi];
                ki = k_in[2*pi + 1];

                qrc = fp32_mul_bits(qr, c);
                qis = fp32_mul_bits(qi, s);
                qrs = fp32_mul_bits(qr, s);
                qic = fp32_mul_bits(qi, c);
                krc = fp32_mul_bits(kr, c);
                kis = fp32_mul_bits(ki, s);
                krs = fp32_mul_bits(kr, s);
                kic = fp32_mul_bits(ki, c);

                q_re = fp32_add_bits(qrc, fp32_neg(qis));
                q_im = fp32_add_bits(qrs, qic);
                k_re = fp32_add_bits(krc, fp32_neg(kis));
                k_im = fp32_add_bits(krs, kic);

                q_out_next[2*pi]     = q_re;
                q_out_next[2*pi + 1] = q_im;
                k_out_next[2*pi]     = k_re;
                k_out_next[2*pi + 1] = k_im;
            end
        end
    end

    integer ri;
    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            out_q_pe_rot_0  <= 32'd0; out_q_pe_rot_1  <= 32'd0;
            out_q_pe_rot_2  <= 32'd0; out_q_pe_rot_3  <= 32'd0;
            out_q_pe_rot_4  <= 32'd0; out_q_pe_rot_5  <= 32'd0;
            out_q_pe_rot_6  <= 32'd0; out_q_pe_rot_7  <= 32'd0;
            out_q_pe_rot_8  <= 32'd0; out_q_pe_rot_9  <= 32'd0;
            out_q_pe_rot_10 <= 32'd0; out_q_pe_rot_11 <= 32'd0;
            out_q_pe_rot_12 <= 32'd0; out_q_pe_rot_13 <= 32'd0;
            out_q_pe_rot_14 <= 32'd0; out_q_pe_rot_15 <= 32'd0;
            out_k_pe_rot_0  <= 32'd0; out_k_pe_rot_1  <= 32'd0;
            out_k_pe_rot_2  <= 32'd0; out_k_pe_rot_3  <= 32'd0;
            out_k_pe_rot_4  <= 32'd0; out_k_pe_rot_5  <= 32'd0;
            out_k_pe_rot_6  <= 32'd0; out_k_pe_rot_7  <= 32'd0;
            out_k_pe_rot_8  <= 32'd0; out_k_pe_rot_9  <= 32'd0;
            out_k_pe_rot_10 <= 32'd0; out_k_pe_rot_11 <= 32'd0;
            out_k_pe_rot_12 <= 32'd0; out_k_pe_rot_13 <= 32'd0;
            out_k_pe_rot_14 <= 32'd0; out_k_pe_rot_15 <= 32'd0;
        end else if (input_accepted) begin
            busy <= 1'b1;
            out_q_pe_rot_0  <= q_out_next[0];  out_q_pe_rot_1  <= q_out_next[1];
            out_q_pe_rot_2  <= q_out_next[2];  out_q_pe_rot_3  <= q_out_next[3];
            out_q_pe_rot_4  <= q_out_next[4];  out_q_pe_rot_5  <= q_out_next[5];
            out_q_pe_rot_6  <= q_out_next[6];  out_q_pe_rot_7  <= q_out_next[7];
            out_q_pe_rot_8  <= q_out_next[8];  out_q_pe_rot_9  <= q_out_next[9];
            out_q_pe_rot_10 <= q_out_next[10]; out_q_pe_rot_11 <= q_out_next[11];
            out_q_pe_rot_12 <= q_out_next[12]; out_q_pe_rot_13 <= q_out_next[13];
            out_q_pe_rot_14 <= q_out_next[14]; out_q_pe_rot_15 <= q_out_next[15];
            out_k_pe_rot_0  <= k_out_next[0];  out_k_pe_rot_1  <= k_out_next[1];
            out_k_pe_rot_2  <= k_out_next[2];  out_k_pe_rot_3  <= k_out_next[3];
            out_k_pe_rot_4  <= k_out_next[4];  out_k_pe_rot_5  <= k_out_next[5];
            out_k_pe_rot_6  <= k_out_next[6];  out_k_pe_rot_7  <= k_out_next[7];
            out_k_pe_rot_8  <= k_out_next[8];  out_k_pe_rot_9  <= k_out_next[9];
            out_k_pe_rot_10 <= k_out_next[10]; out_k_pe_rot_11 <= k_out_next[11];
            out_k_pe_rot_12 <= k_out_next[12]; out_k_pe_rot_13 <= k_out_next[13];
            out_k_pe_rot_14 <= k_out_next[14]; out_k_pe_rot_15 <= k_out_next[15];
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
