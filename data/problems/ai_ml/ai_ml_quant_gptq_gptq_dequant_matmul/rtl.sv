module AffineW4Dot32 (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic signed [7:0] in_act_0,
    input logic signed [7:0] in_act_1,
    input logic signed [7:0] in_act_2,
    input logic signed [7:0] in_act_3,
    input logic signed [7:0] in_act_4,
    input logic signed [7:0] in_act_5,
    input logic signed [7:0] in_act_6,
    input logic signed [7:0] in_act_7,
    input logic signed [7:0] in_act_8,
    input logic signed [7:0] in_act_9,
    input logic signed [7:0] in_act_10,
    input logic signed [7:0] in_act_11,
    input logic signed [7:0] in_act_12,
    input logic signed [7:0] in_act_13,
    input logic signed [7:0] in_act_14,
    input logic signed [7:0] in_act_15,
    input logic signed [7:0] in_act_16,
    input logic signed [7:0] in_act_17,
    input logic signed [7:0] in_act_18,
    input logic signed [7:0] in_act_19,
    input logic signed [7:0] in_act_20,
    input logic signed [7:0] in_act_21,
    input logic signed [7:0] in_act_22,
    input logic signed [7:0] in_act_23,
    input logic signed [7:0] in_act_24,
    input logic signed [7:0] in_act_25,
    input logic signed [7:0] in_act_26,
    input logic signed [7:0] in_act_27,
    input logic signed [7:0] in_act_28,
    input logic signed [7:0] in_act_29,
    input logic signed [7:0] in_act_30,
    input logic signed [7:0] in_act_31,
    input logic [31:0] in_qweight_0,
    input logic [31:0] in_qweight_1,
    input logic [31:0] in_qweight_2,
    input logic [31:0] in_qweight_3,
    input logic signed [15:0] in_scale_0,
    input logic signed [15:0] in_scale_1,
    input logic signed [15:0] in_scale_2,
    input logic signed [15:0] in_scale_3,
    input logic signed [7:0] in_zero_0,
    input logic signed [7:0] in_zero_1,
    input logic signed [7:0] in_zero_2,
    input logic signed [7:0] in_zero_3,
    output logic out_valid,
    input logic out_ready,
    output logic signed [31:0] out_sum
);
    wire signed [7:0] acts [0:31];
    wire [31:0] packed_weights [0:3];
    wire signed [15:0] scales [0:3];
    wire signed [7:0] zeros [0:3];
    wire signed [32:0] products [0:31];
    logic signed [39:0] total;
    assign acts[0] = in_act_0;
    assign acts[1] = in_act_1;
    assign acts[2] = in_act_2;
    assign acts[3] = in_act_3;
    assign acts[4] = in_act_4;
    assign acts[5] = in_act_5;
    assign acts[6] = in_act_6;
    assign acts[7] = in_act_7;
    assign acts[8] = in_act_8;
    assign acts[9] = in_act_9;
    assign acts[10] = in_act_10;
    assign acts[11] = in_act_11;
    assign acts[12] = in_act_12;
    assign acts[13] = in_act_13;
    assign acts[14] = in_act_14;
    assign acts[15] = in_act_15;
    assign acts[16] = in_act_16;
    assign acts[17] = in_act_17;
    assign acts[18] = in_act_18;
    assign acts[19] = in_act_19;
    assign acts[20] = in_act_20;
    assign acts[21] = in_act_21;
    assign acts[22] = in_act_22;
    assign acts[23] = in_act_23;
    assign acts[24] = in_act_24;
    assign acts[25] = in_act_25;
    assign acts[26] = in_act_26;
    assign acts[27] = in_act_27;
    assign acts[28] = in_act_28;
    assign acts[29] = in_act_29;
    assign acts[30] = in_act_30;
    assign acts[31] = in_act_31;
    assign packed_weights[0] = in_qweight_0;
    assign packed_weights[1] = in_qweight_1;
    assign packed_weights[2] = in_qweight_2;
    assign packed_weights[3] = in_qweight_3;
    assign scales[0] = in_scale_0;
    assign scales[1] = in_scale_1;
    assign scales[2] = in_scale_2;
    assign scales[3] = in_scale_3;
    assign zeros[0] = in_zero_0;
    assign zeros[1] = in_zero_1;
    assign zeros[2] = in_zero_2;
    assign zeros[3] = in_zero_3;
    // The subtraction, dequantized weight, and lane product retain their
    // full signed widths. Only the final result is reduced modulo 2^32.
    for (genvar i = 0; i < 32; i = i + 1) begin : lanes
        wire [3:0] code = packed_weights[i/8][4*(i%8) +: 4];
        wire signed [8:0] delta = $signed({5'b0,code})
                               - $signed({zeros[i/8][7],zeros[i/8]});
        wire signed [24:0] weight = delta * scales[i/8];
        assign products[i] = acts[i] * weight;
    end
    always_comb begin
        total = 40'sd0;
        for (int i = 0; i < 32; i = i + 1)
            total = total + {{7{products[i][32]}},products[i]};
    end
    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 1'b0;
            out_sum <= 32'sd0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid)
                out_sum <= total[31:0];
        end
    end
endmodule
