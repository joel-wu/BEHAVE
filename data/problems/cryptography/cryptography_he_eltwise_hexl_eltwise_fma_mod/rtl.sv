module cryptography_he_eltwise_hexl_eltwise_fma_mod (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [63:0] in_arg1,
    input  logic [63:0] in_arg2,
    input  logic [63:0] in_arg3,
    input  logic        in_arg3_enable,
    input  logic [63:0] in_modulus,
    input  logic [3:0]  in_input_mod_factor,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_result
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic [63:0] mod_reduce_128_by_64(
    input logic [127:0] dividend,
    input logic [63:0]  modulus
);
    logic [63:0] rem;
    integer i;
begin
    mod_reduce_128_by_64 = 64'd0;
    if (modulus < 64'd2) begin
        mod_reduce_128_by_64 = 64'd0;
    end else begin
        rem = 64'd0;
        for (i = 127; i >= 0; i = i - 1) begin
            if (rem >= (modulus - rem)) begin
                rem = (rem + rem) - modulus;
            end else begin
                rem = rem + rem;
            end

            if (dividend[i]) begin
                if (rem == (modulus - 1'b1)) begin
                    rem = 64'd0;
                end else begin
                    rem = rem + 64'd1;
                end
            end
        end
        mod_reduce_128_by_64 = rem;
    end
end
endfunction

logic [127:0] product_reg;
logic [127:0] total_reg;
logic [63:0]  modulus_reg;
logic [63:0]  result_next;

always_comb begin
    product_reg = {64'd0, in_arg1} * {64'd0, in_arg2};
    total_reg   = product_reg + (in_arg3_enable ? {64'd0, in_arg3} : 128'd0);
    modulus_reg = in_modulus;
    result_next = mod_reduce_128_by_64(total_reg, modulus_reg);
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_result <= 64'd0;
    end else if (input_accepted) begin
        out_result <= result_next;
        busy       <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule