module ai_ml_vision_dwconv_convnext_dw_7x7 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_x00,
    input  logic [15:0]  in_x01,
    input  logic [15:0]  in_x02,
    input  logic [15:0]  in_x03,
    input  logic [15:0]  in_x04,
    input  logic [15:0]  in_x05,
    input  logic [15:0]  in_x06,
    input  logic [15:0]  in_x10,
    input  logic [15:0]  in_x11,
    input  logic [15:0]  in_x12,
    input  logic [15:0]  in_x13,
    input  logic [15:0]  in_x14,
    input  logic [15:0]  in_x15,
    input  logic [15:0]  in_x16,
    input  logic [15:0]  in_x20,
    input  logic [15:0]  in_x21,
    input  logic [15:0]  in_x22,
    input  logic [15:0]  in_x23,
    input  logic [15:0]  in_x24,
    input  logic [15:0]  in_x25,
    input  logic [15:0]  in_x26,
    input  logic [15:0]  in_x30,
    input  logic [15:0]  in_x31,
    input  logic [15:0]  in_x32,
    input  logic [15:0]  in_x33,
    input  logic [15:0]  in_x34,
    input  logic [15:0]  in_x35,
    input  logic [15:0]  in_x36,
    input  logic [15:0]  in_x40,
    input  logic [15:0]  in_x41,
    input  logic [15:0]  in_x42,
    input  logic [15:0]  in_x43,
    input  logic [15:0]  in_x44,
    input  logic [15:0]  in_x45,
    input  logic [15:0]  in_x46,
    input  logic [15:0]  in_x50,
    input  logic [15:0]  in_x51,
    input  logic [15:0]  in_x52,
    input  logic [15:0]  in_x53,
    input  logic [15:0]  in_x54,
    input  logic [15:0]  in_x55,
    input  logic [15:0]  in_x56,
    input  logic [15:0]  in_x60,
    input  logic [15:0]  in_x61,
    input  logic [15:0]  in_x62,
    input  logic [15:0]  in_x63,
    input  logic [15:0]  in_x64,
    input  logic [15:0]  in_x65,
    input  logic [15:0]  in_x66,

    input  logic [15:0]  in_w00,
    input  logic [15:0]  in_w01,
    input  logic [15:0]  in_w02,
    input  logic [15:0]  in_w03,
    input  logic [15:0]  in_w04,
    input  logic [15:0]  in_w05,
    input  logic [15:0]  in_w06,
    input  logic [15:0]  in_w10,
    input  logic [15:0]  in_w11,
    input  logic [15:0]  in_w12,
    input  logic [15:0]  in_w13,
    input  logic [15:0]  in_w14,
    input  logic [15:0]  in_w15,
    input  logic [15:0]  in_w16,
    input  logic [15:0]  in_w20,
    input  logic [15:0]  in_w21,
    input  logic [15:0]  in_w22,
    input  logic [15:0]  in_w23,
    input  logic [15:0]  in_w24,
    input  logic [15:0]  in_w25,
    input  logic [15:0]  in_w26,
    input  logic [15:0]  in_w30,
    input  logic [15:0]  in_w31,
    input  logic [15:0]  in_w32,
    input  logic [15:0]  in_w33,
    input  logic [15:0]  in_w34,
    input  logic [15:0]  in_w35,
    input  logic [15:0]  in_w36,
    input  logic [15:0]  in_w40,
    input  logic [15:0]  in_w41,
    input  logic [15:0]  in_w42,
    input  logic [15:0]  in_w43,
    input  logic [15:0]  in_w44,
    input  logic [15:0]  in_w45,
    input  logic [15:0]  in_w46,
    input  logic [15:0]  in_w50,
    input  logic [15:0]  in_w51,
    input  logic [15:0]  in_w52,
    input  logic [15:0]  in_w53,
    input  logic [15:0]  in_w54,
    input  logic [15:0]  in_w55,
    input  logic [15:0]  in_w56,
    input  logic [15:0]  in_w60,
    input  logic [15:0]  in_w61,
    input  logic [15:0]  in_w62,
    input  logic [15:0]  in_w63,
    input  logic [15:0]  in_w64,
    input  logic [15:0]  in_w65,
    input  logic [15:0]  in_w66,

    input  logic [31:0]  in_bias,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_y
);

logic busy;
wire  input_accepted;
wire  output_consumed;

logic signed [63:0] sum_comb;
logic [31:0]        out_y_next;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    sum_comb = $signed(in_bias);

    sum_comb = sum_comb + $signed(in_x00) * $signed(in_w00);
    sum_comb = sum_comb + $signed(in_x01) * $signed(in_w01);
    sum_comb = sum_comb + $signed(in_x02) * $signed(in_w02);
    sum_comb = sum_comb + $signed(in_x03) * $signed(in_w03);
    sum_comb = sum_comb + $signed(in_x04) * $signed(in_w04);
    sum_comb = sum_comb + $signed(in_x05) * $signed(in_w05);
    sum_comb = sum_comb + $signed(in_x06) * $signed(in_w06);

    sum_comb = sum_comb + $signed(in_x10) * $signed(in_w10);
    sum_comb = sum_comb + $signed(in_x11) * $signed(in_w11);
    sum_comb = sum_comb + $signed(in_x12) * $signed(in_w12);
    sum_comb = sum_comb + $signed(in_x13) * $signed(in_w13);
    sum_comb = sum_comb + $signed(in_x14) * $signed(in_w14);
    sum_comb = sum_comb + $signed(in_x15) * $signed(in_w15);
    sum_comb = sum_comb + $signed(in_x16) * $signed(in_w16);

    sum_comb = sum_comb + $signed(in_x20) * $signed(in_w20);
    sum_comb = sum_comb + $signed(in_x21) * $signed(in_w21);
    sum_comb = sum_comb + $signed(in_x22) * $signed(in_w22);
    sum_comb = sum_comb + $signed(in_x23) * $signed(in_w23);
    sum_comb = sum_comb + $signed(in_x24) * $signed(in_w24);
    sum_comb = sum_comb + $signed(in_x25) * $signed(in_w25);
    sum_comb = sum_comb + $signed(in_x26) * $signed(in_w26);

    sum_comb = sum_comb + $signed(in_x30) * $signed(in_w30);
    sum_comb = sum_comb + $signed(in_x31) * $signed(in_w31);
    sum_comb = sum_comb + $signed(in_x32) * $signed(in_w32);
    sum_comb = sum_comb + $signed(in_x33) * $signed(in_w33);
    sum_comb = sum_comb + $signed(in_x34) * $signed(in_w34);
    sum_comb = sum_comb + $signed(in_x35) * $signed(in_w35);
    sum_comb = sum_comb + $signed(in_x36) * $signed(in_w36);

    sum_comb = sum_comb + $signed(in_x40) * $signed(in_w40);
    sum_comb = sum_comb + $signed(in_x41) * $signed(in_w41);
    sum_comb = sum_comb + $signed(in_x42) * $signed(in_w42);
    sum_comb = sum_comb + $signed(in_x43) * $signed(in_w43);
    sum_comb = sum_comb + $signed(in_x44) * $signed(in_w44);
    sum_comb = sum_comb + $signed(in_x45) * $signed(in_w45);
    sum_comb = sum_comb + $signed(in_x46) * $signed(in_w46);

    sum_comb = sum_comb + $signed(in_x50) * $signed(in_w50);
    sum_comb = sum_comb + $signed(in_x51) * $signed(in_w51);
    sum_comb = sum_comb + $signed(in_x52) * $signed(in_w52);
    sum_comb = sum_comb + $signed(in_x53) * $signed(in_w53);
    sum_comb = sum_comb + $signed(in_x54) * $signed(in_w54);
    sum_comb = sum_comb + $signed(in_x55) * $signed(in_w55);
    sum_comb = sum_comb + $signed(in_x56) * $signed(in_w56);

    sum_comb = sum_comb + $signed(in_x60) * $signed(in_w60);
    sum_comb = sum_comb + $signed(in_x61) * $signed(in_w61);
    sum_comb = sum_comb + $signed(in_x62) * $signed(in_w62);
    sum_comb = sum_comb + $signed(in_x63) * $signed(in_w63);
    sum_comb = sum_comb + $signed(in_x64) * $signed(in_w64);
    sum_comb = sum_comb + $signed(in_x65) * $signed(in_w65);
    sum_comb = sum_comb + $signed(in_x66) * $signed(in_w66);

    out_y_next = sum_comb[31:0];
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy  <= 1'b0;
        out_y <= 32'h00000000;
    end else if (input_accepted) begin
        busy  <= 1'b1;
        out_y <= out_y_next;
    end else if (output_consumed) begin
        busy  <= 1'b0;
        out_y <= 32'h00000000;
    end
end

endmodule