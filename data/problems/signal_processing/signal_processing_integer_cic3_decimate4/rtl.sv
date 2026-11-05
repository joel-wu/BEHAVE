// SPDX-License-Identifier: Apache-2.0
// BEHAVE fixed-block CIC adaptation; source attribution and terms are in NOTICE.
module CIC3Decimate4 (
    input  logic        clk, rst, in_valid, out_ready,
    output logic        in_ready, out_valid,
    input  logic [63:0] in_samples,
    output logic [21:0] out_values
);
    logic [31:0] i0, i1, i2, c0, c1, c2;
    logic [31:0] next_i0, next_i1, next_i2, d0, d1, d2, sample;
    logic [21:0] result;

    always_comb begin
        next_i0 = i0;
        next_i1 = i1;
        next_i2 = i2;
        sample = 0;
        for (integer lane = 0; lane < 4; lane = lane + 1) begin
            sample = {{16{in_samples[16*lane+15]}}, in_samples[16*lane +: 16]};
            next_i0 = next_i0 + sample;
            next_i1 = next_i1 + next_i0;
            next_i2 = next_i2 + next_i1;
        end
        d0 = next_i2 - c0;
        d1 = d0 - c1;
        d2 = d1 - c2;
        result = d2[21:0];
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 0;
            out_values <= 0;
            i0 <= 0;
            i1 <= 0;
            i2 <= 0;
            c0 <= 0;
            c1 <= 0;
            c2 <= 0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin
                out_values <= result;
                i0 <= next_i0;
                i1 <= next_i1;
                i2 <= next_i2;
                c0 <= next_i2;
                c1 <= d0;
                c2 <= d1;
            end
        end
    end
endmodule
