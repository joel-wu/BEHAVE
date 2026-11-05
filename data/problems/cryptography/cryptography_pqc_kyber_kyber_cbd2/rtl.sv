module cryptography_pqc_kyber_kyber_cbd2 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_b0,
    input  logic [7:0]  in_b1,
    input  logic [7:0]  in_b2,
    input  logic [7:0]  in_b3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [3:0]  out_coeff
);

    logic        busy;
    logic [2:0]  out_idx;
    logic [3:0]  coeff_buf0;
    logic [3:0]  coeff_buf1;
    logic [3:0]  coeff_buf2;
    logic [3:0]  coeff_buf3;
    logic [3:0]  coeff_buf4;
    logic [3:0]  coeff_buf5;
    logic [3:0]  coeff_buf6;
    logic [3:0]  coeff_buf7;

    logic [31:0] t_word;
    logic [31:0] d_word;

    logic [1:0]  a0, b0c;
    logic [1:0]  a1, b1c;
    logic [1:0]  a2, b2c;
    logic [1:0]  a3, b3c;
    logic [1:0]  a4, b4c;
    logic [1:0]  a5, b5c;
    logic [1:0]  a6, b6c;
    logic [1:0]  a7, b7c;

    logic signed [2:0] coeff0_s;
    logic signed [2:0] coeff1_s;
    logic signed [2:0] coeff2_s;
    logic signed [2:0] coeff3_s;
    logic signed [2:0] coeff4_s;
    logic signed [2:0] coeff5_s;
    logic signed [2:0] coeff6_s;
    logic signed [2:0] coeff7_s;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;
    wire frame_complete  = output_consumed & (out_idx == 3'd7);

    assign out_valid = busy;
    assign in_ready  = (~busy) | frame_complete;

    always_comb begin
        unique case (out_idx)
            3'd0: out_coeff = coeff_buf0;
            3'd1: out_coeff = coeff_buf1;
            3'd2: out_coeff = coeff_buf2;
            3'd3: out_coeff = coeff_buf3;
            3'd4: out_coeff = coeff_buf4;
            3'd5: out_coeff = coeff_buf5;
            3'd6: out_coeff = coeff_buf6;
            3'd7: out_coeff = coeff_buf7;
        endcase
    end

    always_comb begin
        t_word = {in_b3, in_b2, in_b1, in_b0};
        d_word = (t_word & 32'h55555555) + ((t_word >> 1) & 32'h55555555);

        a0 = d_word[1:0];
        b0c = d_word[3:2];
        a1 = d_word[5:4];
        b1c = d_word[7:6];
        a2 = d_word[9:8];
        b2c = d_word[11:10];
        a3 = d_word[13:12];
        b3c = d_word[15:14];
        a4 = d_word[17:16];
        b4c = d_word[19:18];
        a5 = d_word[21:20];
        b5c = d_word[23:22];
        a6 = d_word[25:24];
        b6c = d_word[27:26];
        a7 = d_word[29:28];
        b7c = d_word[31:30];

        coeff0_s = $signed({1'b0, a0}) - $signed({1'b0, b0c});
        coeff1_s = $signed({1'b0, a1}) - $signed({1'b0, b1c});
        coeff2_s = $signed({1'b0, a2}) - $signed({1'b0, b2c});
        coeff3_s = $signed({1'b0, a3}) - $signed({1'b0, b3c});
        coeff4_s = $signed({1'b0, a4}) - $signed({1'b0, b4c});
        coeff5_s = $signed({1'b0, a5}) - $signed({1'b0, b5c});
        coeff6_s = $signed({1'b0, a6}) - $signed({1'b0, b6c});
        coeff7_s = $signed({1'b0, a7}) - $signed({1'b0, b7c});
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            out_idx   <= 3'd0;
            coeff_buf0 <= 4'd0;
            coeff_buf1 <= 4'd0;
            coeff_buf2 <= 4'd0;
            coeff_buf3 <= 4'd0;
            coeff_buf4 <= 4'd0;
            coeff_buf5 <= 4'd0;
            coeff_buf6 <= 4'd0;
            coeff_buf7 <= 4'd0;
        end else if (input_accepted) begin
            coeff_buf0 <= {coeff0_s[2], coeff0_s};
            coeff_buf1 <= {coeff1_s[2], coeff1_s};
            coeff_buf2 <= {coeff2_s[2], coeff2_s};
            coeff_buf3 <= {coeff3_s[2], coeff3_s};
            coeff_buf4 <= {coeff4_s[2], coeff4_s};
            coeff_buf5 <= {coeff5_s[2], coeff5_s};
            coeff_buf6 <= {coeff6_s[2], coeff6_s};
            coeff_buf7 <= {coeff7_s[2], coeff7_s};
            out_idx    <= 3'd0;
            busy       <= 1'b1;
        end else if (output_consumed) begin
            if (out_idx == 3'd7) begin
                out_idx <= 3'd0;
                busy    <= 1'b0;
            end else begin
                out_idx <= out_idx + 3'd1;
                busy    <= 1'b1;
            end
        end
    end

endmodule
