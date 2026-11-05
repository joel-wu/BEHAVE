module Poly1305FinalReduce (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_h0,
    input  logic [31:0]  in_h1,
    input  logic [31:0]  in_h2,
    input  logic [31:0]  in_h3,
    input  logic [31:0]  in_h4,
    input  logic [31:0]  in_s0,
    input  logic [31:0]  in_s1,
    input  logic [31:0]  in_s2,
    input  logic [31:0]  in_s3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_h0,
    output logic [31:0]  out_h1,
    output logic [31:0]  out_h2,
    output logic [31:0]  out_h3
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    logic [32:0] t_g0_0, t_g1_0, t_g2_0, t_g3_0;
    logic [31:0] g0_0, g1_0, g2_0, g3_0, g4_0;
    logic        c0_0, c1_0, c2_0, c3_0;
    logic        sel0;
    logic [31:0] h0_1, h1_1, h2_1, h3_1, h4_1;

    logic [32:0] t_g0_1, t_g1_1, t_g2_1, t_g3_1;
    logic [31:0] g0_1, g1_1, g2_1, g3_1, g4_1;
    logic        c0_1, c1_1, c2_1, c3_1;
    logic        sel1;
    logic [31:0] h0_r, h1_r, h2_r, h3_r, h4_r;

    logic [32:0] sum0, sum1, sum2, sum3;

    always_comb begin
        t_g0_0 = {1'b0, in_h0} + 33'd5;
        g0_0   = t_g0_0[31:0];
        c0_0   = t_g0_0[32];

        t_g1_0 = {1'b0, in_h1} + {32'd0, c0_0};
        g1_0   = t_g1_0[31:0];
        c1_0   = t_g1_0[32];

        t_g2_0 = {1'b0, in_h2} + {32'd0, c1_0};
        g2_0   = t_g2_0[31:0];
        c2_0   = t_g2_0[32];

        t_g3_0 = {1'b0, in_h3} + {32'd0, c2_0};
        g3_0   = t_g3_0[31:0];
        c3_0   = t_g3_0[32];

        g4_0 = in_h4 + {31'd0, c3_0} - 32'd4;

        sel0 = ~g4_0[31];

        if (sel0) begin
            h0_1 = g0_0;
            h1_1 = g1_0;
            h2_1 = g2_0;
            h3_1 = g3_0;
            h4_1 = g4_0;
        end else begin
            h0_1 = in_h0;
            h1_1 = in_h1;
            h2_1 = in_h2;
            h3_1 = in_h3;
            h4_1 = in_h4;
        end

        t_g0_1 = {1'b0, h0_1} + 33'd5;
        g0_1   = t_g0_1[31:0];
        c0_1   = t_g0_1[32];

        t_g1_1 = {1'b0, h1_1} + {32'd0, c0_1};
        g1_1   = t_g1_1[31:0];
        c1_1   = t_g1_1[32];

        t_g2_1 = {1'b0, h2_1} + {32'd0, c1_1};
        g2_1   = t_g2_1[31:0];
        c2_1   = t_g2_1[32];

        t_g3_1 = {1'b0, h3_1} + {32'd0, c2_1};
        g3_1   = t_g3_1[31:0];
        c3_1   = t_g3_1[32];

        g4_1 = h4_1 + {31'd0, c3_1} - 32'd4;

        sel1 = ~g4_1[31];

        if (sel1) begin
            h0_r = g0_1;
            h1_r = g1_1;
            h2_r = g2_1;
            h3_r = g3_1;
            h4_r = g4_1;
        end else begin
            h0_r = h0_1;
            h1_r = h1_1;
            h2_r = h2_1;
            h3_r = h3_1;
            h4_r = h4_1;
        end

        sum0 = {1'b0, h0_r} + {1'b0, in_s0};
        sum1 = {1'b0, h1_r} + {1'b0, in_s1} + {32'd0, sum0[32]};
        sum2 = {1'b0, h2_r} + {1'b0, in_s2} + {32'd0, sum1[32]};
        sum3 = {1'b0, h3_r} + {1'b0, in_s3} + {32'd0, sum2[32]};
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy   <= 1'b0;
            out_h0 <= 32'd0;
            out_h1 <= 32'd0;
            out_h2 <= 32'd0;
            out_h3 <= 32'd0;
        end else if (input_accepted) begin
            out_h0 <= sum0[31:0];
            out_h1 <= sum1[31:0];
            out_h2 <= sum2[31:0];
            out_h3 <= sum3[31:0];
            busy   <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule