/* verilator lint_off WIDTH */
/* verilator lint_off WIDTHEXPAND */
/* verilator lint_off WIDTHTRUNC */
/* verilator lint_off SELRANGE */
module ai_ml_tx_pos_rope_rope_standard (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_position_id,
    input  logic [31:0]  in_rope_theta,
    input  logic [255:0] in_q,
    input  logic [255:0] in_k,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_q_rot,
    output logic [255:0] out_k_rot
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
        fp32_neg = {~x[31], x[30:0]};
    endfunction

    // ----- LUT (16 positions × 4 frequencies) -----
    function automatic logic [31:0] cos_lut(input logic [3:0] p, input logic [1:0] j);
        case ({p, j})
            6'd0:  cos_lut = 32'h3F800000; 6'd1:  cos_lut = 32'h3F800000;
            6'd2:  cos_lut = 32'h3F800000; 6'd3:  cos_lut = 32'h3F800000;
            6'd4:  cos_lut = 32'h3F0A5140; 6'd5:  cos_lut = 32'h3F7EB898;
            6'd6:  cos_lut = 32'h3F7FFCB9; 6'd7:  cos_lut = 32'h3F7FFFF8;
            6'd8:  cos_lut = 32'hBED51133; 6'd9:  cos_lut = 32'h3F7AE5A5;
            6'd10: cos_lut = 32'h3F7FF2E5; 6'd11: cos_lut = 32'h3F7FFFDE;
            6'd12: cos_lut = 32'hBF7D7026; 6'd13: cos_lut = 32'h3F7490EF;
            6'd14: cos_lut = 32'h3F7FE283; 6'd15: cos_lut = 32'h3F7FFFB5;
            6'd16: cos_lut = 32'hBF275530; 6'd17: cos_lut = 32'h3F6BCAA7;
            6'd18: cos_lut = 32'h3F7FCB94; 6'd19: cos_lut = 32'h3F7FFF7A;
            6'd20: cos_lut = 32'h3E913C2C; 6'd21: cos_lut = 32'h3F60A940;
            6'd22: cos_lut = 32'h3F7FAE19; 6'd23: cos_lut = 32'h3F7FFF2E;
            6'd24: cos_lut = 32'h3F75CDB8; 6'd25: cos_lut = 32'h3F534932;
            6'd26: cos_lut = 32'h3F7F8A12; 6'd27: cos_lut = 32'h3F7FFED2;
            6'd28: cos_lut = 32'h3F40FFBD; 6'd29: cos_lut = 32'h3F43CCB3;
            6'd30: cos_lut = 32'h3F7F5F81; 6'd31: cos_lut = 32'h3F7FFE65;
            6'd32: cos_lut = 32'hBE14FDF6; 6'd33: cos_lut = 32'h3F325B5F;
            6'd34: cos_lut = 32'h3F7F2E66; 6'd35: cos_lut = 32'h3F7FFDE7;
            6'd36: cos_lut = 32'hBF693FD5; 6'd37: cos_lut = 32'h3F1F21D5;
            6'd38: cos_lut = 32'h3F7EF6C2; 6'd39: cos_lut = 32'h3F7FFD59;
            6'd40: cos_lut = 32'hBF56CD64; 6'd41: cos_lut = 32'h3F0A5140;
            6'd42: cos_lut = 32'h3F7EB898; 6'd43: cos_lut = 32'h3F7FFCB9;
            6'd44: cos_lut = 32'h3B910572; 6'd45: cos_lut = 32'h3EE83DC0;
            6'd46: cos_lut = 32'h3F7E73E8; 6'd47: cos_lut = 32'h3F7FFC09;
            6'd48: cos_lut = 32'h3F5806D0; 6'd49: cos_lut = 32'h3EB986F5;
            6'd50: cos_lut = 32'h3F7E28B5; 6'd51: cos_lut = 32'h3F7FFB48;
            6'd52: cos_lut = 32'h3F684E6F; 6'd53: cos_lut = 32'h3E88F59B;
            6'd54: cos_lut = 32'h3F7DD700; 6'd55: cos_lut = 32'h3F7FFA76;
            6'd56: cos_lut = 32'h3E0C04D7; 6'd57: cos_lut = 32'h3E2E0BDE;
            6'd58: cos_lut = 32'h3F7D7ECC; 6'd59: cos_lut = 32'h3F7FF994;
            6'd60: cos_lut = 32'hBF427AE8; 6'd61: cos_lut = 32'h3D90DEAA;
            6'd62: cos_lut = 32'h3F7D201A; 6'd63: cos_lut = 32'h3F7FF8A1;
            default: cos_lut = 32'd0;
        endcase
    endfunction

    function automatic logic [31:0] sin_lut(input logic [3:0] p, input logic [1:0] j);
        case ({p, j})
            6'd0:  sin_lut = 32'h00000000; 6'd1:  sin_lut = 32'h00000000;
            6'd2:  sin_lut = 32'h00000000; 6'd3:  sin_lut = 32'h00000000;
            6'd4:  sin_lut = 32'h3F576AA4; 6'd5:  sin_lut = 32'h3DCC7576;
            6'd6:  sin_lut = 32'h3C23D657; 6'd7:  sin_lut = 32'h3A83126D;
            6'd8:  sin_lut = 32'h3F68C7B7; 6'd9:  sin_lut = 32'h3E4B6FF9;
            6'd10: sin_lut = 32'h3CA3D43E; 6'd11: sin_lut = 32'h3B031269;
            6'd12: sin_lut = 32'h3E1081C3; 6'd13: sin_lut = 32'h3E974E6D;
            6'd14: sin_lut = 32'h3CF5B920; 6'd15: sin_lut = 32'h3B449B93;
            6'd16: sin_lut = 32'hBF41BDCF; 6'd17: sin_lut = 32'h3EC761D7;
            6'd18: sin_lut = 32'h3D23CBDB; 6'd19: sin_lut = 32'h3B831258;
            6'd20: sin_lut = 32'hBF757C10; 6'd21: sin_lut = 32'h3EF57744;
            6'd22: sin_lut = 32'h3D4CB6F5; 6'd23: sin_lut = 32'h3BA3D6DE;
            6'd24: sin_lut = 32'hBE8F0F8C; 6'd25: sin_lut = 32'h3F108C69;
            6'd26: sin_lut = 32'h3D759CD1; 6'd27: sin_lut = 32'h3BC49B59;
            6'd28: sin_lut = 32'h3F283046; 6'd29: sin_lut = 32'h3F24EB73;
            6'd30: sin_lut = 32'h3D8F3E32; 6'd31: sin_lut = 32'h3BE55FC7;
            6'd32: sin_lut = 32'h3F7D4695; 6'd33: sin_lut = 32'h3F37A4A6;
            6'd34: sin_lut = 32'h3DA3AA51; 6'd35: sin_lut = 32'h3C031213;
            6'd36: sin_lut = 32'h3ED30132; 6'd37: sin_lut = 32'h3F48881D;
            6'd38: sin_lut = 32'h3DB8123F; 6'd39: sin_lut = 32'h3C13743A;
            6'd40: sin_lut = 32'hBF0B44F8; 6'd41: sin_lut = 32'h3F576AA4;
            6'd42: sin_lut = 32'h3DCC7576; 6'd43: sin_lut = 32'h3C23D657;
            6'd44: sin_lut = 32'hBF7FFF5C; 6'd45: sin_lut = 32'h3F64262A;
            6'd46: sin_lut = 32'h3DE0D372; 6'd47: sin_lut = 32'h3C34386A;
            6'd48: sin_lut = 32'hBF095CD8; 6'd49: sin_lut = 32'h3F6E9A1D;
            6'd50: sin_lut = 32'h3DF52BAC; 6'd51: sin_lut = 32'h3C449A71;
            6'd52: sin_lut = 32'h3ED72022; 6'd53: sin_lut = 32'h3F76ABC0;
            6'd54: sin_lut = 32'h3E04BED0; 6'd55: sin_lut = 32'h3C54FC6B;
            6'd56: sin_lut = 32'h3F7D9872; 6'd57: sin_lut = 32'h3F7C466F;
            6'd58: sin_lut = 32'h3E0EE464; 6'd59: sin_lut = 32'h3C655E56;
            6'd60: sin_lut = 32'h3F267944; 6'd61: sin_lut = 32'h3F7F5BD5;
            6'd62: sin_lut = 32'h3E19064F; 6'd63: sin_lut = 32'h3C75C033;
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

    logic [255:0] q_next, k_next;

    function automatic logic [31:0] rotate_lane(input logic [31:0] x, input logic [31:0] xr, input logic [31:0] cs, input logic [31:0] sn);
        logic [31:0] mc, ms;
        begin
            mc = fp32_mul_bits(x, cs);
            ms = fp32_mul_bits(xr, sn);
            rotate_lane = fp32_add_bits(mc, ms);
        end
    endfunction

    integer ri;
    always_comb begin
        logic [31:0] q [0:7];
        logic [31:0] k [0:7];
        logic [31:0] qrh [0:7];
        logic [31:0] krh [0:7];
        logic [31:0] cv [0:7];
        logic [31:0] sv [0:7];
        logic [31:0] q_rot [0:7];
        logic [31:0] k_rot [0:7];
        logic [3:0] pos4;
        logic       in_domain;

        q_next = 256'h0;
        k_next = 256'h0;
        in_domain = (in_rope_theta == THETA_BITS) && (in_position_id <= 32'd15);

        if (in_domain) begin
            pos4 = in_position_id[3:0];
            for (ri = 0; ri < 8; ri = ri + 1) begin
                q[ri] = in_q[32*ri +: 32];
                k[ri] = in_k[32*ri +: 32];
            end
            // cos/sin lanes 0..3 = lut[pos][0..3]; lanes 4..7 = lut[pos][0..3]
            cv[0] = cos_lut(pos4, 2'd0); cv[1] = cos_lut(pos4, 2'd1);
            cv[2] = cos_lut(pos4, 2'd2); cv[3] = cos_lut(pos4, 2'd3);
            cv[4] = cv[0]; cv[5] = cv[1]; cv[6] = cv[2]; cv[7] = cv[3];
            sv[0] = sin_lut(pos4, 2'd0); sv[1] = sin_lut(pos4, 2'd1);
            sv[2] = sin_lut(pos4, 2'd2); sv[3] = sin_lut(pos4, 2'd3);
            sv[4] = sv[0]; sv[5] = sv[1]; sv[6] = sv[2]; sv[7] = sv[3];

            qrh[0] = fp32_neg(q[4]);
            qrh[1] = fp32_neg(q[5]);
            qrh[2] = fp32_neg(q[6]);
            qrh[3] = fp32_neg(q[7]);
            qrh[4] = q[0];
            qrh[5] = q[1];
            qrh[6] = q[2];
            qrh[7] = q[3];

            krh[0] = fp32_neg(k[4]);
            krh[1] = fp32_neg(k[5]);
            krh[2] = fp32_neg(k[6]);
            krh[3] = fp32_neg(k[7]);
            krh[4] = k[0];
            krh[5] = k[1];
            krh[6] = k[2];
            krh[7] = k[3];

            for (ri = 0; ri < 8; ri = ri + 1) begin
                q_rot[ri] = rotate_lane(q[ri], qrh[ri], cv[ri], sv[ri]);
                k_rot[ri] = rotate_lane(k[ri], krh[ri], cv[ri], sv[ri]);
            end
            for (ri = 0; ri < 8; ri = ri + 1) begin
                q_next[32*ri +: 32] = q_rot[ri];
                k_next[32*ri +: 32] = k_rot[ri];
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            out_q_rot <= 256'h0;
            out_k_rot <= 256'h0;
        end else if (input_accepted) begin
            busy      <= 1'b1;
            out_q_rot <= q_next;
            out_k_rot <= k_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
