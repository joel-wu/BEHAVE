module cryptography_pqc_dilithium_dilithium_ntt_butterfly (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_a0,
    input  logic [31:0]  in_a1,
    input  logic [31:0]  in_a2,
    input  logic [31:0]  in_a3,
    input  logic [31:0]  in_a4,
    input  logic [31:0]  in_a5,
    input  logic [31:0]  in_a6,
    input  logic [31:0]  in_a7,
    input  logic [31:0]  in_b0,
    input  logic [31:0]  in_b1,
    input  logic [31:0]  in_b2,
    input  logic [31:0]  in_b3,
    input  logic [31:0]  in_b4,
    input  logic [31:0]  in_b5,
    input  logic [31:0]  in_b6,
    input  logic [31:0]  in_b7,
    input  logic [31:0]  in_zeta,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_a0,
    output logic [31:0]  out_a1,
    output logic [31:0]  out_a2,
    output logic [31:0]  out_a3,
    output logic [31:0]  out_a4,
    output logic [31:0]  out_a5,
    output logic [31:0]  out_a6,
    output logic [31:0]  out_a7,
    output logic [31:0]  out_b0,
    output logic [31:0]  out_b1,
    output logic [31:0]  out_b2,
    output logic [31:0]  out_b3,
    output logic [31:0]  out_b4,
    output logic [31:0]  out_b5,
    output logic [31:0]  out_b6,
    output logic [31:0]  out_b7
);

    localparam logic [31:0] Q    = 32'd8380417;
    localparam logic [31:0] QINV = 32'd58728449;

    logic busy;

    wire input_accepted;
    wire output_consumed;

    logic signed [31:0] a0_s, a1_s, a2_s, a3_s, a4_s, a5_s, a6_s, a7_s;
    logic signed [31:0] b0_s, b1_s, b2_s, b3_s, b4_s, b5_s, b6_s, b7_s;
    logic signed [31:0] zeta_s;

    logic signed [31:0] t0, t1, t2, t3, t4, t5, t6, t7;
    logic signed [63:0] x0, x1, x2, x3, x4, x5, x6, x7;

    function automatic logic signed [31:0] montgomery_reduce(input logic signed [63:0] x);
        logic [63:0] x_u64;
        logic [95:0] prod_u;
        logic [31:0] u;
        logic signed [31:0] u_s;
        logic signed [63:0] uq;
        logic signed [63:0] y;
        logic signed [31:0] result32;
        begin
            x_u64 = x;
            prod_u = x_u64 * QINV;
            u = prod_u[31:0];
            u_s = u;
            uq = u_s * $signed({1'b0, Q});
            y = x - uq;
            result32 = y >>> 32;
            montgomery_reduce = result32;
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        a0_s = $signed(in_a0);
        a1_s = $signed(in_a1);
        a2_s = $signed(in_a2);
        a3_s = $signed(in_a3);
        a4_s = $signed(in_a4);
        a5_s = $signed(in_a5);
        a6_s = $signed(in_a6);
        a7_s = $signed(in_a7);

        b0_s = $signed(in_b0);
        b1_s = $signed(in_b1);
        b2_s = $signed(in_b2);
        b3_s = $signed(in_b3);
        b4_s = $signed(in_b4);
        b5_s = $signed(in_b5);
        b6_s = $signed(in_b6);
        b7_s = $signed(in_b7);

        zeta_s = $signed(in_zeta);

        x0 = b0_s * zeta_s;
        x1 = b1_s * zeta_s;
        x2 = b2_s * zeta_s;
        x3 = b3_s * zeta_s;
        x4 = b4_s * zeta_s;
        x5 = b5_s * zeta_s;
        x6 = b6_s * zeta_s;
        x7 = b7_s * zeta_s;

        t0 = montgomery_reduce(x0);
        t1 = montgomery_reduce(x1);
        t2 = montgomery_reduce(x2);
        t3 = montgomery_reduce(x3);
        t4 = montgomery_reduce(x4);
        t5 = montgomery_reduce(x5);
        t6 = montgomery_reduce(x6);
        t7 = montgomery_reduce(x7);
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy    <= 1'b0;
            out_a0  <= 32'd0;
            out_a1  <= 32'd0;
            out_a2  <= 32'd0;
            out_a3  <= 32'd0;
            out_a4  <= 32'd0;
            out_a5  <= 32'd0;
            out_a6  <= 32'd0;
            out_a7  <= 32'd0;
            out_b0  <= 32'd0;
            out_b1  <= 32'd0;
            out_b2  <= 32'd0;
            out_b3  <= 32'd0;
            out_b4  <= 32'd0;
            out_b5  <= 32'd0;
            out_b6  <= 32'd0;
            out_b7  <= 32'd0;
        end else if (input_accepted) begin
            busy   <= 1'b1;
            out_a0 <= a0_s + t0;
            out_a1 <= a1_s + t1;
            out_a2 <= a2_s + t2;
            out_a3 <= a3_s + t3;
            out_a4 <= a4_s + t4;
            out_a5 <= a5_s + t5;
            out_a6 <= a6_s + t6;
            out_a7 <= a7_s + t7;
            out_b0 <= a0_s - t0;
            out_b1 <= a1_s - t1;
            out_b2 <= a2_s - t2;
            out_b3 <= a3_s - t3;
            out_b4 <= a4_s - t4;
            out_b5 <= a5_s - t5;
            out_b6 <= a6_s - t6;
            out_b7 <= a7_s - t7;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule