module cryptography_sym_sha_sha1_compress_round (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_h0,
    input  logic [31:0]  in_h1,
    input  logic [31:0]  in_h2,
    input  logic [31:0]  in_h3,
    input  logic [31:0]  in_h4,
    input  logic [31:0]  in_w0,
    input  logic [31:0]  in_w1,
    input  logic [31:0]  in_w2,
    input  logic [31:0]  in_w3,
    input  logic [31:0]  in_w4,
    input  logic [31:0]  in_w5,
    input  logic [31:0]  in_w6,
    input  logic [31:0]  in_w7,
    input  logic [31:0]  in_w8,
    input  logic [31:0]  in_w9,
    input  logic [31:0]  in_w10,
    input  logic [31:0]  in_w11,
    input  logic [31:0]  in_w12,
    input  logic [31:0]  in_w13,
    input  logic [31:0]  in_w14,
    input  logic [31:0]  in_w15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_h0,
    output logic [31:0]  out_h1,
    output logic [31:0]  out_h2,
    output logic [31:0]  out_h3,
    output logic [31:0]  out_h4
);

    logic busy;
    logic [6:0] round_idx;

    logic [31:0] init_h0, init_h1, init_h2, init_h3, init_h4;
    logic [31:0] a, b, c, d, e;
    logic [31:0] wbuf [0:15];

    logic [31:0] word_t;
    logic [31:0] f_t;
    logic [31:0] k_t;
    logic [31:0] wt_xor;
    logic [31:0] rotl5_a;
    logic [31:0] rotl30_b;
    logic [31:0] t_sum;

    logic [31:0] next_a, next_b, next_c, next_d, next_e;
    logic [3:0]  idx_t;
    logic [3:0]  idx_m3, idx_m8, idx_m14;
    logic [6:0]  round_m3, round_m8, round_m14;
    logic done;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign done      = (round_idx == 7'd80);
    assign out_valid = busy & done;
    assign in_ready  = ~busy | output_consumed;

    always_comb begin
        round_m3  = round_idx - 7'd3;
        round_m8  = round_idx - 7'd8;
        round_m14 = round_idx - 7'd14;

        idx_t   = round_idx[3:0];
        idx_m3  = round_m3[3:0];
        idx_m8  = round_m8[3:0];
        idx_m14 = round_m14[3:0];

        wt_xor   = wbuf[idx_m3] ^ wbuf[idx_m8] ^ wbuf[idx_m14] ^ wbuf[idx_t];
        rotl5_a  = {a[26:0], a[31:27]};
        rotl30_b = {b[1:0], b[31:2]};

        if (round_idx < 7'd16) begin
            word_t = wbuf[idx_t];
        end else begin
            word_t = {wt_xor[30:0], wt_xor[31]};
        end

        if (round_idx < 7'd20) begin
            f_t = (b & c) ^ ((~b) & d);
            k_t = 32'h5A827999;
        end else if (round_idx < 7'd40) begin
            f_t = b ^ c ^ d;
            k_t = 32'h6ED9EBA1;
        end else if (round_idx < 7'd60) begin
            f_t = (b & c) ^ (b & d) ^ (c & d);
            k_t = 32'h8F1BBCDC;
        end else begin
            f_t = b ^ c ^ d;
            k_t = 32'hCA62C1D6;
        end

        t_sum  = rotl5_a + f_t + e + k_t + word_t;
        next_a = t_sum;
        next_b = a;
        next_c = rotl30_b;
        next_d = c;
        next_e = d;
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy      <= 1'b0;
            round_idx <= 7'd0;
            init_h0   <= 32'h00000000;
            init_h1   <= 32'h00000000;
            init_h2   <= 32'h00000000;
            init_h3   <= 32'h00000000;
            init_h4   <= 32'h00000000;
            a         <= 32'h00000000;
            b         <= 32'h00000000;
            c         <= 32'h00000000;
            d         <= 32'h00000000;
            e         <= 32'h00000000;
            wbuf[0]   <= 32'h00000000;
            wbuf[1]   <= 32'h00000000;
            wbuf[2]   <= 32'h00000000;
            wbuf[3]   <= 32'h00000000;
            wbuf[4]   <= 32'h00000000;
            wbuf[5]   <= 32'h00000000;
            wbuf[6]   <= 32'h00000000;
            wbuf[7]   <= 32'h00000000;
            wbuf[8]   <= 32'h00000000;
            wbuf[9]   <= 32'h00000000;
            wbuf[10]  <= 32'h00000000;
            wbuf[11]  <= 32'h00000000;
            wbuf[12]  <= 32'h00000000;
            wbuf[13]  <= 32'h00000000;
            wbuf[14]  <= 32'h00000000;
            wbuf[15]  <= 32'h00000000;
            out_h0    <= 32'h00000000;
            out_h1    <= 32'h00000000;
            out_h2    <= 32'h00000000;
            out_h3    <= 32'h00000000;
            out_h4    <= 32'h00000000;
        end else begin
            if (input_accepted) begin
                busy      <= 1'b1;
                round_idx <= 7'd0;

                init_h0   <= in_h0;
                init_h1   <= in_h1;
                init_h2   <= in_h2;
                init_h3   <= in_h3;
                init_h4   <= in_h4;

                a         <= in_h0;
                b         <= in_h1;
                c         <= in_h2;
                d         <= in_h3;
                e         <= in_h4;

                wbuf[0]   <= in_w0;
                wbuf[1]   <= in_w1;
                wbuf[2]   <= in_w2;
                wbuf[3]   <= in_w3;
                wbuf[4]   <= in_w4;
                wbuf[5]   <= in_w5;
                wbuf[6]   <= in_w6;
                wbuf[7]   <= in_w7;
                wbuf[8]   <= in_w8;
                wbuf[9]   <= in_w9;
                wbuf[10]  <= in_w10;
                wbuf[11]  <= in_w11;
                wbuf[12]  <= in_w12;
                wbuf[13]  <= in_w13;
                wbuf[14]  <= in_w14;
                wbuf[15]  <= in_w15;
            end else if (output_consumed) begin
                busy      <= 1'b0;
                round_idx <= 7'd0;
            end else if (busy) begin
                if (round_idx < 7'd80) begin
                    a <= next_a;
                    b <= next_b;
                    c <= next_c;
                    d <= next_d;
                    e <= next_e;

                    if (round_idx >= 7'd16) begin
                        wbuf[idx_t] <= word_t;
                    end

                    if (round_idx == 7'd79) begin
                        out_h0 <= init_h0 + next_a;
                        out_h1 <= init_h1 + next_b;
                        out_h2 <= init_h2 + next_c;
                        out_h3 <= init_h3 + next_d;
                        out_h4 <= init_h4 + next_e;
                    end

                    round_idx <= round_idx + 7'd1;
                end
            end
        end
    end

endmodule