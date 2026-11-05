module PoolNormChannelLayerNorm (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [63:0] in_x,
    input logic [63:0] in_weight,
    input logic [63:0] in_bias,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_y
);
    function automatic logic [8:0] isqrt_normalized(input logic [63:0] value);
        logic [8:0] root, trial;
        logic [63:0] trial_wide;
        begin
            root = '0;
            for (int bit_index = 8; bit_index >= 0; bit_index--) begin
                trial = root | (9'd1 << bit_index);
                trial_wide = {55'd0, trial};
                if (trial_wide * trial_wide <= value)
                    root = trial;
            end
            isqrt_normalized = root;
        end
    endfunction

    logic signed [63:0] x [0:3];
    logic signed [63:0] centered [0:3];
    logic signed [63:0] total, radicand, numerator, normalized, affine;
    logic signed [15:0] weight, bias;
    logic [63:0] squared, ratio;
    logic [8:0] magnitude;
    logic [63:0] projected;

    always_comb begin
        total = 64'sd0;
        for (int i = 0; i < 4; i++) begin
            x[i] = {{48{in_x[16*i+15]}}, in_x[16*i +: 16]};
            total = total + x[i];
        end
        radicand = 64'sd64;
        for (int i = 0; i < 4; i++) begin
            centered[i] = 4*x[i] - total;
            radicand = radicand + centered[i]*centered[i];
        end
        projected = '0;
        numerator = '0;
        normalized = '0;
        affine = '0;
        weight = '0;
        bias = '0;
        squared = '0;
        ratio = '0;
        magnitude = '0;
        for (int i = 0; i < 4; i++) begin
            numerator = centered[i]*512;
            squared = $unsigned(numerator*numerator);
            ratio = squared / $unsigned(radicand);
            magnitude = isqrt_normalized(ratio);
            normalized = centered[i] < 0 ? -$signed({55'd0,magnitude}) : $signed({55'd0,magnitude});
            weight = $signed(in_weight[16*i +: 16]);
            bias = $signed(in_bias[16*i +: 16]);
            affine = (normalized * $signed({{48{weight[15]}},weight})) / 64'sd256
                     + $signed({{48{bias[15]}},bias});
            if (affine > 32767)
                projected[16*i +: 16] = 16'h7fff;
            else if (affine < -32768)
                projected[16*i +: 16] = 16'h8000;
            else
                projected[16*i +: 16] = affine[15:0];
        end
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            out_valid <= 1'b0;
            out_y <= '0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid)
                out_y <= projected;
        end
    end
endmodule
