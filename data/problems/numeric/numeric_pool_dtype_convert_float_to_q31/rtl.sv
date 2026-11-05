module float_to_q31 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_q31
);

    logic busy;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [31:0] convert_float_to_q31(input logic [31:0] x);
        logic [7:0]  exp;
        logic [22:0] frac;
        logic [23:0] mant;
        logic [31:0] mag;
        begin
            exp   = x[30:23];
            frac  = x[22:0];
            mant  = {1'b1, frac};
            mag   = 32'd0;
            convert_float_to_q31 = 32'h0000_0000;

            if ((exp == 8'hFF) && (frac != 23'd0)) begin
                convert_float_to_q31 = 32'h0000_0000;
            end else if (exp >= 8'd127) begin
                // |x| >= 1 (including infinity): saturate before shifting.
                convert_float_to_q31 = x[31] ? 32'h8000_0000 : 32'h7FFF_FFFF;
            end else if (exp >= 8'd96) begin
                // 2^-31 <= |x| < 1. The scaled magnitude fits in 31 bits;
                // left shifts are 0..7 and right shifts are 1..23.
                if (exp >= 8'd119)
                    mag = {8'd0, mant} << (exp - 8'd119);
                else
                    mag = {8'd0, mant} >> (8'd119 - exp);
                convert_float_to_q31 = x[31] ? (~mag + 32'd1) : mag;
            end
        end
    endfunction

    always_ff @(posedge clk) begin
        if (rst) begin
            busy    <= 1'b0;
            out_q31 <= 32'h0000_0000;
        end else if (input_accepted) begin
            busy    <= 1'b1;
            out_q31 <= convert_float_to_q31(in_x);
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
