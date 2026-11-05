module Bn254FrMontgomeryMultiply (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [253:0] in_a,
    input logic [253:0] in_b,
    output logic out_valid,
    input logic out_ready,
    output logic [253:0] out_c,
    output logic out_error
);
    logic pending;
    logic [253:0] out_c_next;
    logic out_error_next;
    assign in_ready = !pending || out_ready;
    assign out_valid = pending && !rst;
    localparam logic [255:0] P = 256'd21888242871839275222246405745257275088548364400416034343698204186575808495617;
    localparam logic [255:0] N0 = 256'h73f82f1d0d8341b2e39a9828990623916586864b4c6911b3c2e1f593efffffff;
    logic [511:0] t, mp;
    logic [255:0] m;
    logic [512:0] sum;
    logic [256:0] u;

    always_comb begin
        t = 512'(in_a) * in_b;
        m = t[255:0] * N0;
        mp = 512'(m) * P;
        sum = {1'b0, t} + {1'b0, mp};
        u = sum[512:256];
        out_error_next = in_a >= P || in_b >= P;
        out_c_next = out_error_next ? 254'd0 : 254'(u >= P ? u-P : u);
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            pending <= 1'b0;
            out_c <= '0;
            out_error <= '0;
        end else if (in_ready) begin
            pending <= in_valid;
            if (in_valid) begin
                out_c <= out_c_next;
                out_error <= out_error_next;
            end
        end
    end
endmodule
