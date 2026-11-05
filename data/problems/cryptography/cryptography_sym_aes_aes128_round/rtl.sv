module aes128_round (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_state,
    input  logic [127:0] in_round_key,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [127:0] out_state
);

    logic busy;
    wire  input_accepted;
    wire  output_consumed;

    function automatic [7:0] aes_sbox(input [7:0] x);
        begin
            aes_sbox = 8'h00;
            case (x)
                8'h00: aes_sbox = 8'h63; 8'h01: aes_sbox = 8'h7C; 8'h02: aes_sbox = 8'h77; 8'h03: aes_sbox = 8'h7B;
                8'h04: aes_sbox = 8'hF2; 8'h05: aes_sbox = 8'h6B; 8'h06: aes_sbox = 8'h6F; 8'h07: aes_sbox = 8'hC5;
                8'h08: aes_sbox = 8'h30; 8'h09: aes_sbox = 8'h01; 8'h0A: aes_sbox = 8'h67; 8'h0B: aes_sbox = 8'h2B;
                8'h0C: aes_sbox = 8'hFE; 8'h0D: aes_sbox = 8'hD7; 8'h0E: aes_sbox = 8'hAB; 8'h0F: aes_sbox = 8'h76;
                8'h10: aes_sbox = 8'hCA; 8'h11: aes_sbox = 8'h82; 8'h12: aes_sbox = 8'hC9; 8'h13: aes_sbox = 8'h7D;
                8'h14: aes_sbox = 8'hFA; 8'h15: aes_sbox = 8'h59; 8'h16: aes_sbox = 8'h47; 8'h17: aes_sbox = 8'hF0;
                8'h18: aes_sbox = 8'hAD; 8'h19: aes_sbox = 8'hD4; 8'h1A: aes_sbox = 8'hA2; 8'h1B: aes_sbox = 8'hAF;
                8'h1C: aes_sbox = 8'h9C; 8'h1D: aes_sbox = 8'hA4; 8'h1E: aes_sbox = 8'h72; 8'h1F: aes_sbox = 8'hC0;
                8'h20: aes_sbox = 8'hB7; 8'h21: aes_sbox = 8'hFD; 8'h22: aes_sbox = 8'h93; 8'h23: aes_sbox = 8'h26;
                8'h24: aes_sbox = 8'h36; 8'h25: aes_sbox = 8'h3F; 8'h26: aes_sbox = 8'hF7; 8'h27: aes_sbox = 8'hCC;
                8'h28: aes_sbox = 8'h34; 8'h29: aes_sbox = 8'hA5; 8'h2A: aes_sbox = 8'hE5; 8'h2B: aes_sbox = 8'hF1;
                8'h2C: aes_sbox = 8'h71; 8'h2D: aes_sbox = 8'hD8; 8'h2E: aes_sbox = 8'h31; 8'h2F: aes_sbox = 8'h15;
                8'h30: aes_sbox = 8'h04; 8'h31: aes_sbox = 8'hC7; 8'h32: aes_sbox = 8'h23; 8'h33: aes_sbox = 8'hC3;
                8'h34: aes_sbox = 8'h18; 8'h35: aes_sbox = 8'h96; 8'h36: aes_sbox = 8'h05; 8'h37: aes_sbox = 8'h9A;
                8'h38: aes_sbox = 8'h07; 8'h39: aes_sbox = 8'h12; 8'h3A: aes_sbox = 8'h80; 8'h3B: aes_sbox = 8'hE2;
                8'h3C: aes_sbox = 8'hEB; 8'h3D: aes_sbox = 8'h27; 8'h3E: aes_sbox = 8'hB2; 8'h3F: aes_sbox = 8'h75;
                8'h40: aes_sbox = 8'h09; 8'h41: aes_sbox = 8'h83; 8'h42: aes_sbox = 8'h2C; 8'h43: aes_sbox = 8'h1A;
                8'h44: aes_sbox = 8'h1B; 8'h45: aes_sbox = 8'h6E; 8'h46: aes_sbox = 8'h5A; 8'h47: aes_sbox = 8'hA0;
                8'h48: aes_sbox = 8'h52; 8'h49: aes_sbox = 8'h3B; 8'h4A: aes_sbox = 8'hD6; 8'h4B: aes_sbox = 8'hB3;
                8'h4C: aes_sbox = 8'h29; 8'h4D: aes_sbox = 8'hE3; 8'h4E: aes_sbox = 8'h2F; 8'h4F: aes_sbox = 8'h84;
                8'h50: aes_sbox = 8'h53; 8'h51: aes_sbox = 8'hD1; 8'h52: aes_sbox = 8'h00; 8'h53: aes_sbox = 8'hED;
                8'h54: aes_sbox = 8'h20; 8'h55: aes_sbox = 8'hFC; 8'h56: aes_sbox = 8'hB1; 8'h57: aes_sbox = 8'h5B;
                8'h58: aes_sbox = 8'h6A; 8'h59: aes_sbox = 8'hCB; 8'h5A: aes_sbox = 8'hBE; 8'h5B: aes_sbox = 8'h39;
                8'h5C: aes_sbox = 8'h4A; 8'h5D: aes_sbox = 8'h4C; 8'h5E: aes_sbox = 8'h58; 8'h5F: aes_sbox = 8'hCF;
                8'h60: aes_sbox = 8'hD0; 8'h61: aes_sbox = 8'hEF; 8'h62: aes_sbox = 8'hAA; 8'h63: aes_sbox = 8'hFB;
                8'h64: aes_sbox = 8'h43; 8'h65: aes_sbox = 8'h4D; 8'h66: aes_sbox = 8'h33; 8'h67: aes_sbox = 8'h85;
                8'h68: aes_sbox = 8'h45; 8'h69: aes_sbox = 8'hF9; 8'h6A: aes_sbox = 8'h02; 8'h6B: aes_sbox = 8'h7F;
                8'h6C: aes_sbox = 8'h50; 8'h6D: aes_sbox = 8'h3C; 8'h6E: aes_sbox = 8'h9F; 8'h6F: aes_sbox = 8'hA8;
                8'h70: aes_sbox = 8'h51; 8'h71: aes_sbox = 8'hA3; 8'h72: aes_sbox = 8'h40; 8'h73: aes_sbox = 8'h8F;
                8'h74: aes_sbox = 8'h92; 8'h75: aes_sbox = 8'h9D; 8'h76: aes_sbox = 8'h38; 8'h77: aes_sbox = 8'hF5;
                8'h78: aes_sbox = 8'hBC; 8'h79: aes_sbox = 8'hB6; 8'h7A: aes_sbox = 8'hDA; 8'h7B: aes_sbox = 8'h21;
                8'h7C: aes_sbox = 8'h10; 8'h7D: aes_sbox = 8'hFF; 8'h7E: aes_sbox = 8'hF3; 8'h7F: aes_sbox = 8'hD2;
                8'h80: aes_sbox = 8'hCD; 8'h81: aes_sbox = 8'h0C; 8'h82: aes_sbox = 8'h13; 8'h83: aes_sbox = 8'hEC;
                8'h84: aes_sbox = 8'h5F; 8'h85: aes_sbox = 8'h97; 8'h86: aes_sbox = 8'h44; 8'h87: aes_sbox = 8'h17;
                8'h88: aes_sbox = 8'hC4; 8'h89: aes_sbox = 8'hA7; 8'h8A: aes_sbox = 8'h7E; 8'h8B: aes_sbox = 8'h3D;
                8'h8C: aes_sbox = 8'h64; 8'h8D: aes_sbox = 8'h5D; 8'h8E: aes_sbox = 8'h19; 8'h8F: aes_sbox = 8'h73;
                8'h90: aes_sbox = 8'h60; 8'h91: aes_sbox = 8'h81; 8'h92: aes_sbox = 8'h4F; 8'h93: aes_sbox = 8'hDC;
                8'h94: aes_sbox = 8'h22; 8'h95: aes_sbox = 8'h2A; 8'h96: aes_sbox = 8'h90; 8'h97: aes_sbox = 8'h88;
                8'h98: aes_sbox = 8'h46; 8'h99: aes_sbox = 8'hEE; 8'h9A: aes_sbox = 8'hB8; 8'h9B: aes_sbox = 8'h14;
                8'h9C: aes_sbox = 8'hDE; 8'h9D: aes_sbox = 8'h5E; 8'h9E: aes_sbox = 8'h0B; 8'h9F: aes_sbox = 8'hDB;
                8'hA0: aes_sbox = 8'hE0; 8'hA1: aes_sbox = 8'h32; 8'hA2: aes_sbox = 8'h3A; 8'hA3: aes_sbox = 8'h0A;
                8'hA4: aes_sbox = 8'h49; 8'hA5: aes_sbox = 8'h06; 8'hA6: aes_sbox = 8'h24; 8'hA7: aes_sbox = 8'h5C;
                8'hA8: aes_sbox = 8'hC2; 8'hA9: aes_sbox = 8'hD3; 8'hAA: aes_sbox = 8'hAC; 8'hAB: aes_sbox = 8'h62;
                8'hAC: aes_sbox = 8'h91; 8'hAD: aes_sbox = 8'h95; 8'hAE: aes_sbox = 8'hE4; 8'hAF: aes_sbox = 8'h79;
                8'hB0: aes_sbox = 8'hE7; 8'hB1: aes_sbox = 8'hC8; 8'hB2: aes_sbox = 8'h37; 8'hB3: aes_sbox = 8'h6D;
                8'hB4: aes_sbox = 8'h8D; 8'hB5: aes_sbox = 8'hD5; 8'hB6: aes_sbox = 8'h4E; 8'hB7: aes_sbox = 8'hA9;
                8'hB8: aes_sbox = 8'h6C; 8'hB9: aes_sbox = 8'h56; 8'hBA: aes_sbox = 8'hF4; 8'hBB: aes_sbox = 8'hEA;
                8'hBC: aes_sbox = 8'h65; 8'hBD: aes_sbox = 8'h7A; 8'hBE: aes_sbox = 8'hAE; 8'hBF: aes_sbox = 8'h08;
                8'hC0: aes_sbox = 8'hBA; 8'hC1: aes_sbox = 8'h78; 8'hC2: aes_sbox = 8'h25; 8'hC3: aes_sbox = 8'h2E;
                8'hC4: aes_sbox = 8'h1C; 8'hC5: aes_sbox = 8'hA6; 8'hC6: aes_sbox = 8'hB4; 8'hC7: aes_sbox = 8'hC6;
                8'hC8: aes_sbox = 8'hE8; 8'hC9: aes_sbox = 8'hDD; 8'hCA: aes_sbox = 8'h74; 8'hCB: aes_sbox = 8'h1F;
                8'hCC: aes_sbox = 8'h4B; 8'hCD: aes_sbox = 8'hBD; 8'hCE: aes_sbox = 8'h8B; 8'hCF: aes_sbox = 8'h8A;
                8'hD0: aes_sbox = 8'h70; 8'hD1: aes_sbox = 8'h3E; 8'hD2: aes_sbox = 8'hB5; 8'hD3: aes_sbox = 8'h66;
                8'hD4: aes_sbox = 8'h48; 8'hD5: aes_sbox = 8'h03; 8'hD6: aes_sbox = 8'hF6; 8'hD7: aes_sbox = 8'h0E;
                8'hD8: aes_sbox = 8'h61; 8'hD9: aes_sbox = 8'h35; 8'hDA: aes_sbox = 8'h57; 8'hDB: aes_sbox = 8'hB9;
                8'hDC: aes_sbox = 8'h86; 8'hDD: aes_sbox = 8'hC1; 8'hDE: aes_sbox = 8'h1D; 8'hDF: aes_sbox = 8'h9E;
                8'hE0: aes_sbox = 8'hE1; 8'hE1: aes_sbox = 8'hF8; 8'hE2: aes_sbox = 8'h98; 8'hE3: aes_sbox = 8'h11;
                8'hE4: aes_sbox = 8'h69; 8'hE5: aes_sbox = 8'hD9; 8'hE6: aes_sbox = 8'h8E; 8'hE7: aes_sbox = 8'h94;
                8'hE8: aes_sbox = 8'h9B; 8'hE9: aes_sbox = 8'h1E; 8'hEA: aes_sbox = 8'h87; 8'hEB: aes_sbox = 8'hE9;
                8'hEC: aes_sbox = 8'hCE; 8'hED: aes_sbox = 8'h55; 8'hEE: aes_sbox = 8'h28; 8'hEF: aes_sbox = 8'hDF;
                8'hF0: aes_sbox = 8'h8C; 8'hF1: aes_sbox = 8'hA1; 8'hF2: aes_sbox = 8'h89; 8'hF3: aes_sbox = 8'h0D;
                8'hF4: aes_sbox = 8'hBF; 8'hF5: aes_sbox = 8'hE6; 8'hF6: aes_sbox = 8'h42; 8'hF7: aes_sbox = 8'h68;
                8'hF8: aes_sbox = 8'h41; 8'hF9: aes_sbox = 8'h99; 8'hFA: aes_sbox = 8'h2D; 8'hFB: aes_sbox = 8'h0F;
                8'hFC: aes_sbox = 8'hB0; 8'hFD: aes_sbox = 8'h54; 8'hFE: aes_sbox = 8'hBB; 8'hFF: aes_sbox = 8'h16;
            endcase
        end
    endfunction

    function automatic [7:0] xtime(input [7:0] x);
        reg [7:0] shifted;
        begin
            shifted = {x[6:0], 1'b0};
            if (x[7]) begin
                xtime = shifted ^ 8'h1B;
            end else begin
                xtime = shifted;
            end
        end
    endfunction

    function automatic [127:0] aes_round_func(
        input [127:0] state_in,
        input [127:0] round_key_in
    );
        reg [7:0] s0;  reg [7:0] s1;  reg [7:0] s2;  reg [7:0] s3;
        reg [7:0] s4;  reg [7:0] s5;  reg [7:0] s6;  reg [7:0] s7;
        reg [7:0] s8;  reg [7:0] s9;  reg [7:0] s10; reg [7:0] s11;
        reg [7:0] s12; reg [7:0] s13; reg [7:0] s14; reg [7:0] s15;

        reg [7:0] k0;  reg [7:0] k1;  reg [7:0] k2;  reg [7:0] k3;
        reg [7:0] k4;  reg [7:0] k5;  reg [7:0] k6;  reg [7:0] k7;
        reg [7:0] k8;  reg [7:0] k9;  reg [7:0] k10; reg [7:0] k11;
        reg [7:0] k12; reg [7:0] k13; reg [7:0] k14; reg [7:0] k15;

        reg [7:0] b0;  reg [7:0] b1;  reg [7:0] b2;  reg [7:0] b3;
        reg [7:0] b4;  reg [7:0] b5;  reg [7:0] b6;  reg [7:0] b7;
        reg [7:0] b8;  reg [7:0] b9;  reg [7:0] b10; reg [7:0] b11;
        reg [7:0] b12; reg [7:0] b13; reg [7:0] b14; reg [7:0] b15;

        reg [7:0] sh0;  reg [7:0] sh1;  reg [7:0] sh2;  reg [7:0] sh3;
        reg [7:0] sh4;  reg [7:0] sh5;  reg [7:0] sh6;  reg [7:0] sh7;
        reg [7:0] sh8;  reg [7:0] sh9;  reg [7:0] sh10; reg [7:0] sh11;
        reg [7:0] sh12; reg [7:0] sh13; reg [7:0] sh14; reg [7:0] sh15;

        reg [7:0] m0;  reg [7:0] m1;  reg [7:0] m2;  reg [7:0] m3;
        reg [7:0] m4;  reg [7:0] m5;  reg [7:0] m6;  reg [7:0] m7;
        reg [7:0] m8;  reg [7:0] m9;  reg [7:0] m10; reg [7:0] m11;
        reg [7:0] m12; reg [7:0] m13; reg [7:0] m14; reg [7:0] m15;

        begin
            s0  = state_in[127:120]; s1  = state_in[119:112]; s2  = state_in[111:104]; s3  = state_in[103:96];
            s4  = state_in[95:88];   s5  = state_in[87:80];   s6  = state_in[79:72];   s7  = state_in[71:64];
            s8  = state_in[63:56];   s9  = state_in[55:48];   s10 = state_in[47:40];   s11 = state_in[39:32];
            s12 = state_in[31:24];   s13 = state_in[23:16];   s14 = state_in[15:8];    s15 = state_in[7:0];

            k0  = round_key_in[127:120]; k1  = round_key_in[119:112]; k2  = round_key_in[111:104]; k3  = round_key_in[103:96];
            k4  = round_key_in[95:88];   k5  = round_key_in[87:80];   k6  = round_key_in[79:72];   k7  = round_key_in[71:64];
            k8  = round_key_in[63:56];   k9  = round_key_in[55:48];   k10 = round_key_in[47:40];   k11 = round_key_in[39:32];
            k12 = round_key_in[31:24];   k13 = round_key_in[23:16];   k14 = round_key_in[15:8];    k15 = round_key_in[7:0];

            b0  = aes_sbox(s0);  b1  = aes_sbox(s1);  b2  = aes_sbox(s2);  b3  = aes_sbox(s3);
            b4  = aes_sbox(s4);  b5  = aes_sbox(s5);  b6  = aes_sbox(s6);  b7  = aes_sbox(s7);
            b8  = aes_sbox(s8);  b9  = aes_sbox(s9);  b10 = aes_sbox(s10); b11 = aes_sbox(s11);
            b12 = aes_sbox(s12); b13 = aes_sbox(s13); b14 = aes_sbox(s14); b15 = aes_sbox(s15);

            sh0  = b0;
            sh1  = b5;
            sh2  = b10;
            sh3  = b15;
            sh4  = b4;
            sh5  = b9;
            sh6  = b14;
            sh7  = b3;
            sh8  = b8;
            sh9  = b13;
            sh10 = b2;
            sh11 = b7;
            sh12 = b12;
            sh13 = b1;
            sh14 = b6;
            sh15 = b11;

            m0  = xtime(sh0)  ^ (xtime(sh1)  ^ sh1)  ^ sh2  ^ sh3;
            m1  = sh0 ^ xtime(sh1)  ^ (xtime(sh2)  ^ sh2)  ^ sh3;
            m2  = sh0 ^ sh1 ^ xtime(sh2)  ^ (xtime(sh3)  ^ sh3);
            m3  = (xtime(sh0)  ^ sh0) ^ sh1 ^ sh2 ^ xtime(sh3);

            m4  = xtime(sh4)  ^ (xtime(sh5)  ^ sh5)  ^ sh6  ^ sh7;
            m5  = sh4 ^ xtime(sh5)  ^ (xtime(sh6)  ^ sh6)  ^ sh7;
            m6  = sh4 ^ sh5 ^ xtime(sh6)  ^ (xtime(sh7)  ^ sh7);
            m7  = (xtime(sh4)  ^ sh4) ^ sh5 ^ sh6 ^ xtime(sh7);

            m8  = xtime(sh8)  ^ (xtime(sh9)  ^ sh9)  ^ sh10 ^ sh11;
            m9  = sh8 ^ xtime(sh9)  ^ (xtime(sh10) ^ sh10) ^ sh11;
            m10 = sh8 ^ sh9 ^ xtime(sh10) ^ (xtime(sh11) ^ sh11);
            m11 = (xtime(sh8)  ^ sh8) ^ sh9 ^ sh10 ^ xtime(sh11);

            m12 = xtime(sh12) ^ (xtime(sh13) ^ sh13) ^ sh14 ^ sh15;
            m13 = sh12 ^ xtime(sh13) ^ (xtime(sh14) ^ sh14) ^ sh15;
            m14 = sh12 ^ sh13 ^ xtime(sh14) ^ (xtime(sh15) ^ sh15);
            m15 = (xtime(sh12) ^ sh12) ^ sh13 ^ sh14 ^ xtime(sh15);

            aes_round_func = {
                (m0  ^ k0),  (m1  ^ k1),  (m2  ^ k2),  (m3  ^ k3),
                (m4  ^ k4),  (m5  ^ k5),  (m6  ^ k6),  (m7  ^ k7),
                (m8  ^ k8),  (m9  ^ k9),  (m10 ^ k10), (m11 ^ k11),
                (m12 ^ k12), (m13 ^ k13), (m14 ^ k14), (m15 ^ k15)
            };
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            out_state <= 128'h00000000000000000000000000000000;
        end else if (input_accepted) begin
            out_state <= aes_round_func(in_state, in_round_key);
            busy      <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule