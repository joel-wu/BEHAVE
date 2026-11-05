// SPDX-License-Identifier: Apache-2.0
// BEHAVE fixed-width adaptation; source attribution and terms are in NOTICE.
module Hadamard8 (
    input  logic         clk, rst, in_valid, out_ready,
    output logic         in_ready, out_valid,
    input  logic [127:0] in_samples,
    output logic [151:0] out_values
);
    logic [151:0] result;
    logic signed [18:0] a[0:7], b[0:7], c[0:7], d[0:7];

    always_comb begin
        for (integer i = 0; i < 8; i = i + 1)
            a[i] = {{3{in_samples[16*i+15]}}, in_samples[16*i +: 16]};
        for (integer i = 0; i < 4; i = i + 1) begin
            b[2*i] = a[2*i] + a[2*i+1];
            b[2*i+1] = a[2*i] - a[2*i+1];
        end
        for (integer i = 0; i < 2; i = i + 1) begin
            for (integer j = 0; j < 2; j = j + 1) begin
                c[4*i+j] = b[4*i+j] + b[4*i+j+2];
                c[4*i+j+2] = b[4*i+j] - b[4*i+j+2];
            end
        end
        for (integer i = 0; i < 4; i = i + 1) begin
            d[i] = c[i] + c[i+4];
            d[i+4] = c[i] - c[i+4];
        end
        for (integer i = 0; i < 8; i = i + 1)
            result[19*i +: 19] = d[i];
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 0;
            out_values <= 0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) out_values <= result;
        end
    end
endmodule
