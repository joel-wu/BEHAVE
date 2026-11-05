module uint192_by_uint64_divider (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_numerator_0,
    input  logic [63:0]  in_numerator_1,
    input  logic [63:0]  in_numerator_2,
    input  logic [63:0]  in_denominator,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_quotient_0,
    output logic [63:0]  out_quotient_1,
    output logic [63:0]  out_quotient_2,
    output logic [63:0]  out_remainder
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic [127:0] udivrem_64_by_64(
        input logic [63:0] n,
        input logic [63:0] d
    );
        logic [63:0] q;
        logic [63:0] r;
        integer i;
        begin
            q = 64'd0;
            r = 64'd0;
            for (i = 63; i >= 0; i = i - 1) begin
                logic [64:0] r_shift;
                r_shift = {r, n[i]};
                if (r_shift >= {1'b0, d}) begin
                    r = r_shift[63:0] - d;
                    q[i] = 1'b1;
                end else begin
                    r = r_shift[63:0];
                    q[i] = 1'b0;
                end
            end
            udivrem_64_by_64 = {q, r};
        end
    endfunction

    function automatic [127:0] udivrem_128_by_64(
        input logic [63:0] n_hi,
        input logic [63:0] n_lo,
        input logic [63:0] d
    );
        logic [63:0] q;
        logic [63:0] r;
        logic bit_val;
        integer i;
        begin
            q = 64'd0;
            r = 64'd0;
            bit_val = 1'b0;
            for (i = 127; i >= 0; i = i - 1) begin
                if (i >= 64) begin
                    bit_val = n_hi[i-64];
                end else begin
                    bit_val = n_lo[i];
                end

                begin
                    logic [64:0] r_shift;
                    r_shift = {r, bit_val};
                    if (r_shift >= {1'b0, d}) begin
                        r = r_shift[63:0] - d;
                        if (i < 64) begin
                            q[i] = 1'b1;
                        end
                    end else begin
                        r = r_shift[63:0];
                        if (i < 64) begin
                            q[i] = 1'b0;
                        end
                    end
                end
            end
            udivrem_128_by_64 = {q, r};
        end
    endfunction

    always_ff @(posedge clk) begin
        if (rst) begin
            busy           <= 1'b0;
            out_quotient_0 <= 64'd0;
            out_quotient_1 <= 64'd0;
            out_quotient_2 <= 64'd0;
            out_remainder  <= 64'd0;
        end else if (input_accepted) begin
            if (in_denominator == 64'd0) begin
                out_quotient_0 <= 64'd0;
                out_quotient_1 <= 64'd0;
                out_quotient_2 <= 64'd0;
                out_remainder  <= 64'd0;
            end else begin
                logic [127:0] stage2_res;
                logic [127:0] stage1_res;
                logic [127:0] stage0_res;
                logic [63:0] q2;
                logic [63:0] r2;
                logic [63:0] q1;
                logic [63:0] r1;
                logic [63:0] q0;
                logic [63:0] r0;

                stage2_res = udivrem_64_by_64(in_numerator_2, in_denominator);
                q2 = stage2_res[127:64];
                r2 = stage2_res[63:0];

                stage1_res = udivrem_128_by_64(r2, in_numerator_1, in_denominator);
                q1 = stage1_res[127:64];
                r1 = stage1_res[63:0];

                stage0_res = udivrem_128_by_64(r1, in_numerator_0, in_denominator);
                q0 = stage0_res[127:64];
                r0 = stage0_res[63:0];

                out_quotient_0 <= q0;
                out_quotient_1 <= q1;
                out_quotient_2 <= q2;
                out_remainder  <= r0;
            end
            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule