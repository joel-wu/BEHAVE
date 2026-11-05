module cryptography_pqc_dilithium_dilithium_usehint_44 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [22:0] in_a,
    input  logic        in_hint,

    output logic        out_valid,
    output logic out_error,
    input  logic        out_ready,
    output logic [5:0]  out_r
);

    logic busy;
    wire invalid_input = in_a >= 23'd8380417;

    wire input_accepted;
    wire output_consumed;

    logic [23:0] t_calc;
    logic [39:0] mul_calc;
    logic [5:0]  u_calc;
    logic [5:0]  a1_calc;
    logic signed [24:0] a0_pre_calc;
    logic signed [24:0] a0_calc;
    logic [5:0]  r_calc;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        t_calc      = {1'b0, in_a} + 24'd127;
        t_calc      = t_calc >> 7;
        mul_calc    = t_calc * 40'd11275;
        mul_calc    = mul_calc + 40'd8388608;
        u_calc      = mul_calc[29:24];
        if (u_calc > 6'd43) begin
            a1_calc = 6'd0;
        end else begin
            a1_calc = u_calc;
        end

        a0_pre_calc = $signed({1'b0, in_a}) - $signed({19'd0, a1_calc}) * $signed(25'd190464);
        if (a0_pre_calc > 25'sd4190208) begin
            a0_calc = a0_pre_calc - 25'sd8380417;
        end else begin
            a0_calc = a0_pre_calc;
        end

        if (in_hint == 1'b0) begin
            r_calc = a1_calc;
        end else if (a0_calc > 25'sd0) begin
            if (a1_calc == 6'd43) begin
                r_calc = 6'd0;
            end else begin
                r_calc = a1_calc + 6'd1;
            end
        end else begin
            if (a1_calc == 6'd0) begin
                r_calc = 6'd43;
            end else begin
                r_calc = a1_calc - 6'd1;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy  <= 1'b0;
            out_error <= 1'b0;
            out_r <= 6'd0;
        end else if (input_accepted) begin
            busy  <= 1'b1;
            out_error <= invalid_input;
            out_r <= invalid_input ? '0 : (r_calc);
        end else if (output_consumed) begin
            busy <= 1'b0;
            out_error <= 1'b0;
        end
    end

endmodule
