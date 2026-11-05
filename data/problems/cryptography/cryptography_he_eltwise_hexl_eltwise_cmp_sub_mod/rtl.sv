module cryptography_he_eltwise_hexl_eltwise_cmp_sub_mod (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_operand1,
    input  logic [63:0]  in_modulus,
    input  logic [2:0]   in_cmp,
    input  logic [63:0]  in_bound,
    input  logic [63:0]  in_diff,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_result
);

logic busy;

wire input_accepted;
wire output_consumed;

logic        cmp_true;
logic [63:0] mod_x;
logic [63:0] mod_d;
logic [63:0] diff_mod;
logic [63:0] result_calc;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    unique case (in_cmp)
        3'd0: cmp_true = (in_operand1 == in_bound);
        3'd1: cmp_true = (in_operand1 != in_bound);
        3'd2: cmp_true = (in_operand1 <  in_bound);
        3'd3: cmp_true = (in_operand1 <= in_bound);
        3'd4: cmp_true = (in_operand1 >  in_bound);
        3'd5: cmp_true = (in_operand1 >= in_bound);
        3'd6: cmp_true = 1'b0;
        3'd7: cmp_true = 1'b0;
    endcase
end

always_comb begin
    mod_x     = 64'd0;
    mod_d     = 64'd0;
    diff_mod  = 64'd0;
    result_calc = 64'd0;

    if (in_modulus == 64'd0) begin
        result_calc = 64'd0;
    end else begin
        mod_x = in_operand1 % in_modulus;
        if (!cmp_true) begin
            result_calc = mod_x;
        end else begin
            mod_d = in_diff % in_modulus;
            if (mod_x >= mod_d) begin
                result_calc = mod_x - mod_d;
            end else begin
                diff_mod = mod_d - mod_x;
                result_calc = in_modulus - diff_mod;
            end
        end
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_result <= 64'd0;
    end else if (input_accepted) begin
        busy       <= 1'b1;
        out_result <= result_calc;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule