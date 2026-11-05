module ai_ml_tx_norm_rms_mistral_rmsnorm (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [15:0] in_hidden_0,
    input logic [15:0] in_hidden_1,
    input logic [15:0] in_hidden_2,
    input logic [15:0] in_hidden_3,
    input logic [15:0] in_hidden_4,
    input logic [15:0] in_hidden_5,
    input logic [15:0] in_hidden_6,
    input logic [15:0] in_hidden_7,
    input logic [15:0] in_weight_0,
    input logic [15:0] in_weight_1,
    input logic [15:0] in_weight_2,
    input logic [15:0] in_weight_3,
    input logic [15:0] in_weight_4,
    input logic [15:0] in_weight_5,
    input logic [15:0] in_weight_6,
    input logic [15:0] in_weight_7,
    output logic out_valid,
    input logic out_ready,
    output logic [15:0] out_hidden_0,
    output logic [15:0] out_hidden_1,
    output logic [15:0] out_hidden_2,
    output logic [15:0] out_hidden_3,
    output logic [15:0] out_hidden_4,
    output logic [15:0] out_hidden_5,
    output logic [15:0] out_hidden_6,
    output logic [15:0] out_hidden_7
);
    logic full;
    logic signed [63:0] x [0:7], w [0:7];
    logic signed [63:0] normalized, result;
    logic [63:0] total, denominator, quotient, scale, trial;
    logic [15:0] next_y [0:7];
    integer i, bit_index;
    function automatic signed [63:0] trunc15(input logic signed [63:0] v);
        trunc15 = v < 0 ? -((-v) >>> 15) : (v >>> 15);
    endfunction
    function automatic [15:0] sat16(input logic signed [63:0] v);
        sat16 = v > 32767 ? 16'h7fff : v < -32768 ? 16'h8000 : v[15:0];
    endfunction
    always_comb begin
        x[0] = $signed(in_hidden_0); w[0] = $signed(in_weight_0);
        x[1] = $signed(in_hidden_1); w[1] = $signed(in_weight_1);
        x[2] = $signed(in_hidden_2); w[2] = $signed(in_weight_2);
        x[3] = $signed(in_hidden_3); w[3] = $signed(in_weight_3);
        x[4] = $signed(in_hidden_4); w[4] = $signed(in_weight_4);
        x[5] = $signed(in_hidden_5); w[5] = $signed(in_weight_5);
        x[6] = $signed(in_hidden_6); w[6] = $signed(in_weight_6);
        x[7] = $signed(in_hidden_7); w[7] = $signed(in_weight_7);
        total = 0;
        for (i=0; i<8; i=i+1) total = total + x[i]*x[i];
        denominator = (total / 8) + 1074;
        quotient = 64'h1000000000000000 / denominator;
        scale = 0;
        for (bit_index=30; bit_index>=0; bit_index=bit_index-1) begin
            trial = scale | (64'd1 << bit_index);
            if (trial*trial <= quotient) scale = trial;
        end
        normalized = 0; result = 0;
        for (i=0; i<8; i=i+1) begin
            normalized = trunc15(x[i] * $signed(scale));
            result = trunc15(normalized * w[i]);
            next_y[i] = sat16(result);
        end
    end
    assign in_ready = !full || out_ready;
    assign out_valid = full;
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            full <= 0;
            out_hidden_0 <= 0;
            out_hidden_1 <= 0;
            out_hidden_2 <= 0;
            out_hidden_3 <= 0;
            out_hidden_4 <= 0;
            out_hidden_5 <= 0;
            out_hidden_6 <= 0;
            out_hidden_7 <= 0;
        end else if (in_ready) begin
            full <= in_valid;
            if (in_valid) begin
                out_hidden_0 <= next_y[0];
                out_hidden_1 <= next_y[1];
                out_hidden_2 <= next_y[2];
                out_hidden_3 <= next_y[3];
                out_hidden_4 <= next_y[4];
                out_hidden_5 <= next_y[5];
                out_hidden_6 <= next_y[6];
                out_hidden_7 <= next_y[7];
            end
        end
    end
endmodule
