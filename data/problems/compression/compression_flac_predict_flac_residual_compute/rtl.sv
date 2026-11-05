module compression_flac_predict_flac_residual_compute (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_sample,
    input  logic [5:0]  in_order,
    input  logic [5:0]  in_lp_quantization,
    input  logic [31:0] in_qlp_coeff_0,
    input  logic [31:0] in_qlp_coeff_1,
    input  logic [31:0] in_qlp_coeff_2,
    input  logic [31:0] in_qlp_coeff_3,
    input  logic [31:0] in_qlp_coeff_4,
    input  logic [31:0] in_qlp_coeff_5,
    input  logic [31:0] in_qlp_coeff_6,
    input  logic [31:0] in_qlp_coeff_7,
    input  logic [31:0] in_qlp_coeff_8,
    input  logic [31:0] in_qlp_coeff_9,
    input  logic [31:0] in_qlp_coeff_10,
    input  logic [31:0] in_qlp_coeff_11,
    input  logic [31:0] in_qlp_coeff_12,
    input  logic [31:0] in_qlp_coeff_13,
    input  logic [31:0] in_qlp_coeff_14,
    input  logic [31:0] in_qlp_coeff_15,
    input  logic [31:0] in_qlp_coeff_16,
    input  logic [31:0] in_qlp_coeff_17,
    input  logic [31:0] in_qlp_coeff_18,
    input  logic [31:0] in_qlp_coeff_19,
    input  logic [31:0] in_qlp_coeff_20,
    input  logic [31:0] in_qlp_coeff_21,
    input  logic [31:0] in_qlp_coeff_22,
    input  logic [31:0] in_qlp_coeff_23,
    input  logic [31:0] in_qlp_coeff_24,
    input  logic [31:0] in_qlp_coeff_25,
    input  logic [31:0] in_qlp_coeff_26,
    input  logic [31:0] in_qlp_coeff_27,
    input  logic [31:0] in_qlp_coeff_28,
    input  logic [31:0] in_qlp_coeff_29,
    input  logic [31:0] in_qlp_coeff_30,
    input  logic [31:0] in_qlp_coeff_31,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_residual,
    output logic        out_error
);

logic signed [31:0] history [0:31];
logic signed [31:0] coeff [0:31];
logic [5:0] valid_history;
logic signed [31:0] total, predicted;
logic bad_order;

assign coeff[0] = $signed(in_qlp_coeff_0);
assign coeff[1] = $signed(in_qlp_coeff_1);
assign coeff[2] = $signed(in_qlp_coeff_2);
assign coeff[3] = $signed(in_qlp_coeff_3);
assign coeff[4] = $signed(in_qlp_coeff_4);
assign coeff[5] = $signed(in_qlp_coeff_5);
assign coeff[6] = $signed(in_qlp_coeff_6);
assign coeff[7] = $signed(in_qlp_coeff_7);
assign coeff[8] = $signed(in_qlp_coeff_8);
assign coeff[9] = $signed(in_qlp_coeff_9);
assign coeff[10] = $signed(in_qlp_coeff_10);
assign coeff[11] = $signed(in_qlp_coeff_11);
assign coeff[12] = $signed(in_qlp_coeff_12);
assign coeff[13] = $signed(in_qlp_coeff_13);
assign coeff[14] = $signed(in_qlp_coeff_14);
assign coeff[15] = $signed(in_qlp_coeff_15);
assign coeff[16] = $signed(in_qlp_coeff_16);
assign coeff[17] = $signed(in_qlp_coeff_17);
assign coeff[18] = $signed(in_qlp_coeff_18);
assign coeff[19] = $signed(in_qlp_coeff_19);
assign coeff[20] = $signed(in_qlp_coeff_20);
assign coeff[21] = $signed(in_qlp_coeff_21);
assign coeff[22] = $signed(in_qlp_coeff_22);
assign coeff[23] = $signed(in_qlp_coeff_23);
assign coeff[24] = $signed(in_qlp_coeff_24);
assign coeff[25] = $signed(in_qlp_coeff_25);
assign coeff[26] = $signed(in_qlp_coeff_26);
assign coeff[27] = $signed(in_qlp_coeff_27);
assign coeff[28] = $signed(in_qlp_coeff_28);
assign coeff[29] = $signed(in_qlp_coeff_29);
assign coeff[30] = $signed(in_qlp_coeff_30);
assign coeff[31] = $signed(in_qlp_coeff_31);

assign in_ready = !out_valid || out_ready;
assign bad_order = in_order == 0 || in_order > 32;

always_comb begin
    total = 32'sd0;
    for (int i = 0; i < 32; i++) begin
        if (i < int'(in_order))
            total = total + coeff[i] * history[i];
    end
    predicted = total >>> in_lp_quantization;
end

always_ff @(posedge clk) begin
    if (rst) begin
        for (int i = 0; i < 32; i++) history[i] <= 0;
        valid_history <= 0;
        out_valid <= 0;
        out_residual <= 0;
        out_error <= 0;
    end else if (in_ready) begin
        out_valid <= 0;
        if (in_valid) begin
            if (bad_order) begin
                out_valid <= 1;
                out_residual <= 0;
                out_error <= 1;
            end else begin
                if (valid_history >= in_order) begin
                    out_valid <= 1;
                    out_residual <= $signed(in_sample) - predicted;
                    out_error <= 0;
                end
                for (int i = 31; i > 0; i--) history[i] <= history[i-1];
                history[0] <= $signed(in_sample);
                if (valid_history < 32) valid_history <= valid_history + 1'b1;
            end
        end
    end
end
endmodule
