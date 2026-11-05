module sha256_block_compress (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_h0,
    input  logic [31:0]  in_h1,
    input  logic [31:0]  in_h2,
    input  logic [31:0]  in_h3,
    input  logic [31:0]  in_h4,
    input  logic [31:0]  in_h5,
    input  logic [31:0]  in_h6,
    input  logic [31:0]  in_h7,
    input  logic [7:0]   in_b0,
    input  logic [7:0]   in_b1,
    input  logic [7:0]   in_b2,
    input  logic [7:0]   in_b3,
    input  logic [7:0]   in_b4,
    input  logic [7:0]   in_b5,
    input  logic [7:0]   in_b6,
    input  logic [7:0]   in_b7,
    input  logic [7:0]   in_b8,
    input  logic [7:0]   in_b9,
    input  logic [7:0]   in_b10,
    input  logic [7:0]   in_b11,
    input  logic [7:0]   in_b12,
    input  logic [7:0]   in_b13,
    input  logic [7:0]   in_b14,
    input  logic [7:0]   in_b15,
    input  logic [7:0]   in_b16,
    input  logic [7:0]   in_b17,
    input  logic [7:0]   in_b18,
    input  logic [7:0]   in_b19,
    input  logic [7:0]   in_b20,
    input  logic [7:0]   in_b21,
    input  logic [7:0]   in_b22,
    input  logic [7:0]   in_b23,
    input  logic [7:0]   in_b24,
    input  logic [7:0]   in_b25,
    input  logic [7:0]   in_b26,
    input  logic [7:0]   in_b27,
    input  logic [7:0]   in_b28,
    input  logic [7:0]   in_b29,
    input  logic [7:0]   in_b30,
    input  logic [7:0]   in_b31,
    input  logic [7:0]   in_b32,
    input  logic [7:0]   in_b33,
    input  logic [7:0]   in_b34,
    input  logic [7:0]   in_b35,
    input  logic [7:0]   in_b36,
    input  logic [7:0]   in_b37,
    input  logic [7:0]   in_b38,
    input  logic [7:0]   in_b39,
    input  logic [7:0]   in_b40,
    input  logic [7:0]   in_b41,
    input  logic [7:0]   in_b42,
    input  logic [7:0]   in_b43,
    input  logic [7:0]   in_b44,
    input  logic [7:0]   in_b45,
    input  logic [7:0]   in_b46,
    input  logic [7:0]   in_b47,
    input  logic [7:0]   in_b48,
    input  logic [7:0]   in_b49,
    input  logic [7:0]   in_b50,
    input  logic [7:0]   in_b51,
    input  logic [7:0]   in_b52,
    input  logic [7:0]   in_b53,
    input  logic [7:0]   in_b54,
    input  logic [7:0]   in_b55,
    input  logic [7:0]   in_b56,
    input  logic [7:0]   in_b57,
    input  logic [7:0]   in_b58,
    input  logic [7:0]   in_b59,
    input  logic [7:0]   in_b60,
    input  logic [7:0]   in_b61,
    input  logic [7:0]   in_b62,
    input  logic [7:0]   in_b63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_h0,
    output logic [31:0]  out_h1,
    output logic [31:0]  out_h2,
    output logic [31:0]  out_h3,
    output logic [31:0]  out_h4,
    output logic [31:0]  out_h5,
    output logic [31:0]  out_h6,
    output logic [31:0]  out_h7
);

    logic busy;
    logic [31:0] out_h0_r, out_h1_r, out_h2_r, out_h3_r, out_h4_r, out_h5_r, out_h6_r, out_h7_r;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    assign out_h0 = out_h0_r;
    assign out_h1 = out_h1_r;
    assign out_h2 = out_h2_r;
    assign out_h3 = out_h3_r;
    assign out_h4 = out_h4_r;
    assign out_h5 = out_h5_r;
    assign out_h6 = out_h6_r;
    assign out_h7 = out_h7_r;

    function automatic logic [31:0] rotr32(input logic [31:0] x, input integer n);
        logic [31:0] tmp1;
        logic [31:0] tmp2;
        begin
            tmp1 = x >> n;
            tmp2 = x << (32 - n);
            rotr32 = tmp1 | tmp2;
        end
    endfunction

    function automatic logic [31:0] ch32(input logic [31:0] x, input logic [31:0] y, input logic [31:0] z);
        begin
            ch32 = (x & y) ^ ((~x) & z);
        end
    endfunction

    function automatic logic [31:0] maj32(input logic [31:0] x, input logic [31:0] y, input logic [31:0] z);
        begin
            maj32 = (x & y) ^ (x & z) ^ (y & z);
        end
    endfunction

    function automatic logic [31:0] big_sigma0(input logic [31:0] x);
        begin
            big_sigma0 = rotr32(x, 2) ^ rotr32(x, 13) ^ rotr32(x, 22);
        end
    endfunction

    function automatic logic [31:0] big_sigma1(input logic [31:0] x);
        begin
            big_sigma1 = rotr32(x, 6) ^ rotr32(x, 11) ^ rotr32(x, 25);
        end
    endfunction

    function automatic logic [31:0] small_sigma0(input logic [31:0] x);
        begin
            small_sigma0 = rotr32(x, 7) ^ rotr32(x, 18) ^ (x >> 3);
        end
    endfunction

    function automatic logic [31:0] small_sigma1(input logic [31:0] x);
        begin
            small_sigma1 = rotr32(x, 17) ^ rotr32(x, 19) ^ (x >> 10);
        end
    endfunction

    function automatic logic [31:0] k_const(input logic [5:0] idx);
        begin
            unique case (idx)
                6'd0:  k_const = 32'h428A2F98;
                6'd1:  k_const = 32'h71374491;
                6'd2:  k_const = 32'hB5C0FBCF;
                6'd3:  k_const = 32'hE9B5DBA5;
                6'd4:  k_const = 32'h3956C25B;
                6'd5:  k_const = 32'h59F111F1;
                6'd6:  k_const = 32'h923F82A4;
                6'd7:  k_const = 32'hAB1C5ED5;
                6'd8:  k_const = 32'hD807AA98;
                6'd9:  k_const = 32'h12835B01;
                6'd10: k_const = 32'h243185BE;
                6'd11: k_const = 32'h550C7DC3;
                6'd12: k_const = 32'h72BE5D74;
                6'd13: k_const = 32'h80DEB1FE;
                6'd14: k_const = 32'h9BDC06A7;
                6'd15: k_const = 32'hC19BF174;
                6'd16: k_const = 32'hE49B69C1;
                6'd17: k_const = 32'hEFBE4786;
                6'd18: k_const = 32'h0FC19DC6;
                6'd19: k_const = 32'h240CA1CC;
                6'd20: k_const = 32'h2DE92C6F;
                6'd21: k_const = 32'h4A7484AA;
                6'd22: k_const = 32'h5CB0A9DC;
                6'd23: k_const = 32'h76F988DA;
                6'd24: k_const = 32'h983E5152;
                6'd25: k_const = 32'hA831C66D;
                6'd26: k_const = 32'hB00327C8;
                6'd27: k_const = 32'hBF597FC7;
                6'd28: k_const = 32'hC6E00BF3;
                6'd29: k_const = 32'hD5A79147;
                6'd30: k_const = 32'h06CA6351;
                6'd31: k_const = 32'h14292967;
                6'd32: k_const = 32'h27B70A85;
                6'd33: k_const = 32'h2E1B2138;
                6'd34: k_const = 32'h4D2C6DFC;
                6'd35: k_const = 32'h53380D13;
                6'd36: k_const = 32'h650A7354;
                6'd37: k_const = 32'h766A0ABB;
                6'd38: k_const = 32'h81C2C92E;
                6'd39: k_const = 32'h92722C85;
                6'd40: k_const = 32'hA2BFE8A1;
                6'd41: k_const = 32'hA81A664B;
                6'd42: k_const = 32'hC24B8B70;
                6'd43: k_const = 32'hC76C51A3;
                6'd44: k_const = 32'hD192E819;
                6'd45: k_const = 32'hD6990624;
                6'd46: k_const = 32'hF40E3585;
                6'd47: k_const = 32'h106AA070;
                6'd48: k_const = 32'h19A4C116;
                6'd49: k_const = 32'h1E376C08;
                6'd50: k_const = 32'h2748774C;
                6'd51: k_const = 32'h34B0BCB5;
                6'd52: k_const = 32'h391C0CB3;
                6'd53: k_const = 32'h4ED8AA4A;
                6'd54: k_const = 32'h5B9CCA4F;
                6'd55: k_const = 32'h682E6FF3;
                6'd56: k_const = 32'h748F82EE;
                6'd57: k_const = 32'h78A5636F;
                6'd58: k_const = 32'h84C87814;
                6'd59: k_const = 32'h8CC70208;
                6'd60: k_const = 32'h90BEFFFA;
                6'd61: k_const = 32'hA4506CEB;
                6'd62: k_const = 32'hBEF9A3F7;
                6'd63: k_const = 32'hC67178F2;
            endcase
        end
    endfunction

    logic [31:0] w [0:63];
    logic [31:0] a_var, b_var, c_var, d_var, e_var, f_var, g_var, h_var;
    logic [31:0] t1, t2;
    integer i;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy     <= 1'b0;
            out_h0_r <= 32'd0;
            out_h1_r <= 32'd0;
            out_h2_r <= 32'd0;
            out_h3_r <= 32'd0;
            out_h4_r <= 32'd0;
            out_h5_r <= 32'd0;
            out_h6_r <= 32'd0;
            out_h7_r <= 32'd0;
        end else if (input_accepted) begin
            w[0]  = {in_b0,  in_b1,  in_b2,  in_b3};
            w[1]  = {in_b4,  in_b5,  in_b6,  in_b7};
            w[2]  = {in_b8,  in_b9,  in_b10, in_b11};
            w[3]  = {in_b12, in_b13, in_b14, in_b15};
            w[4]  = {in_b16, in_b17, in_b18, in_b19};
            w[5]  = {in_b20, in_b21, in_b22, in_b23};
            w[6]  = {in_b24, in_b25, in_b26, in_b27};
            w[7]  = {in_b28, in_b29, in_b30, in_b31};
            w[8]  = {in_b32, in_b33, in_b34, in_b35};
            w[9]  = {in_b36, in_b37, in_b38, in_b39};
            w[10] = {in_b40, in_b41, in_b42, in_b43};
            w[11] = {in_b44, in_b45, in_b46, in_b47};
            w[12] = {in_b48, in_b49, in_b50, in_b51};
            w[13] = {in_b52, in_b53, in_b54, in_b55};
            w[14] = {in_b56, in_b57, in_b58, in_b59};
            w[15] = {in_b60, in_b61, in_b62, in_b63};

            for (i = 16; i < 64; i = i + 1) begin
                w[i] = small_sigma1(w[i-2]) + w[i-7] + small_sigma0(w[i-15]) + w[i-16];
            end

            a_var = in_h0;
            b_var = in_h1;
            c_var = in_h2;
            d_var = in_h3;
            e_var = in_h4;
            f_var = in_h5;
            g_var = in_h6;
            h_var = in_h7;

            for (i = 0; i < 64; i = i + 1) begin
                t1 = h_var + big_sigma1(e_var) + ch32(e_var, f_var, g_var) + k_const(i[5:0]) + w[i];
                t2 = big_sigma0(a_var) + maj32(a_var, b_var, c_var);

                h_var = g_var;
                g_var = f_var;
                f_var = e_var;
                e_var = d_var + t1;
                d_var = c_var;
                c_var = b_var;
                b_var = a_var;
                a_var = t1 + t2;
            end

            out_h0_r <= in_h0 + a_var;
            out_h1_r <= in_h1 + b_var;
            out_h2_r <= in_h2 + c_var;
            out_h3_r <= in_h3 + d_var;
            out_h4_r <= in_h4 + e_var;
            out_h5_r <= in_h5 + f_var;
            out_h6_r <= in_h6 + g_var;
            out_h7_r <= in_h7 + h_var;
            busy     <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
