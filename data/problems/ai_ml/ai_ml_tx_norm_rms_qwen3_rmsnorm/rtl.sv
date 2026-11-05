module ai_ml_tx_norm_rms_qwen3_rmsnorm (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [15:0] in_x0,
    input logic [15:0] in_x1,
    input logic [15:0] in_x2,
    input logic [15:0] in_x3,
    input logic [15:0] in_w0,
    input logic [15:0] in_w1,
    input logic [15:0] in_w2,
    input logic [15:0] in_w3,
    output logic out_valid,
    input logic out_ready,
    output logic [15:0] out_y0,
    output logic [15:0] out_y1,
    output logic [15:0] out_y2,
    output logic [15:0] out_y3
);
    logic full;
    logic signed [63:0] x [0:3], w [0:3];
    logic signed [63:0] normalized, result;
    logic [63:0] total, denominator, quotient, scale, trial;
    logic [15:0] next_y [0:3];
    integer i, bit_index;
    function automatic signed [63:0] trunc15(input logic signed [63:0] v);
        trunc15 = v < 0 ? -((-v) >>> 15) : (v >>> 15);
    endfunction
    function automatic [15:0] sat16(input logic signed [63:0] v);
        sat16 = v > 32767 ? 16'h7fff : v < -32768 ? 16'h8000 : v[15:0];
    endfunction
    always_comb begin
        x[0] = $signed(in_x0); w[0] = $signed(in_w0);
        x[1] = $signed(in_x1); w[1] = $signed(in_w1);
        x[2] = $signed(in_x2); w[2] = $signed(in_w2);
        x[3] = $signed(in_x3); w[3] = $signed(in_w3);
        total = 0;
        for (i=0; i<4; i=i+1) total = total + x[i]*x[i];
        denominator = (total / 4) + 1074;
        quotient = 64'h1000000000000000 / denominator;
        scale = 0;
        for (bit_index=30; bit_index>=0; bit_index=bit_index-1) begin
            trial = scale | (64'd1 << bit_index);
            if (trial*trial <= quotient) scale = trial;
        end
        normalized = 0; result = 0;
        for (i=0; i<4; i=i+1) begin
            normalized = trunc15(x[i] * $signed(scale));
            result = trunc15(normalized * w[i]);
            next_y[i] = sat16(result);
        end
    end
    assign in_ready = !full || out_ready;
    assign out_valid = full;
    always_ff @(posedge clk) begin
        if (rst) begin
            full <= 0;
            out_y0 <= 0;
            out_y1 <= 0;
            out_y2 <= 0;
            out_y3 <= 0;
        end else if (in_ready) begin
            full <= in_valid;
            if (in_valid) begin
                out_y0 <= next_y[0];
                out_y1 <= next_y[1];
                out_y2 <= next_y[2];
                out_y3 <= next_y[3];
            end
        end
    end
endmodule
