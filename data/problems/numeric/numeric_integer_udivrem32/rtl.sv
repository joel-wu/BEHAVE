// SPDX-License-Identifier: Apache-2.0
// BEHAVE fixed-width adaptation; source attribution and terms are in NOTICE.
module UDivRem32 (
    input  logic        clk, rst, in_valid, out_ready,
    output logic        in_ready, out_valid,
    input  logic [31:0] in_n, in_d,
    output logic [63:0] out_values
);
    logic [63:0] result;
    logic [31:0] quotient;
    logic [32:0] remainder;

    always_comb begin
        quotient = 0;
        remainder = 0;
        if (in_d == 0) begin
            quotient = 32'hffffffff;
            remainder = {1'b0, in_n};
        end else begin
            for (integer bit_index = 31; bit_index >= 0; bit_index = bit_index - 1) begin
                remainder = {remainder[31:0], in_n[bit_index]};
                if (remainder >= {1'b0, in_d}) begin
                    remainder = remainder - {1'b0, in_d};
                    quotient[bit_index] = 1'b1;
                end
            end
        end
        result = {remainder[31:0], quotient};
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
