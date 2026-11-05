module float_to_q15 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_f32,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_q15
);

    logic busy;
    wire  output_consumed;
    wire  input_accepted;

    assign output_consumed = out_valid & out_ready;
    assign input_accepted  = in_valid & in_ready;
    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy;

    function automatic logic [15:0] convert_f32_to_q15(input logic [31:0] f);
        logic        sign;
        logic [7:0]  exp;
        logic [22:0] frac;
        logic [31:0] prod;
        logic        p_sign;
        logic [7:0]  p_exp;
        logic [22:0] p_frac;
        integer      e;
        logic [23:0] mant;
        logic [54:0] mag;
        logic signed [55:0] val;
        logic signed [15:0] q15;
        begin
            sign  = f[31];
            exp   = f[30:23];
            frac  = f[22:0];
            prod  = 32'h0;
            p_sign = 1'b0;
            p_exp  = 8'h00;
            p_frac = 23'h0;
            e      = 0;
            mant   = 24'h0;
            mag    = 55'd0;
            val    = 56'sd0;
            q15    = 16'sd0;
            convert_f32_to_q15 = 16'h0000;

            if ((exp == 8'hFF) && (frac != 23'd0)) begin
                q15 = 16'sd32767;
            end else if ((exp == 8'hFF) && (frac == 23'd0)) begin
                if (sign) q15 = -16'sd32768;
                else      q15 =  16'sd32767;
            end else begin
                // fp32_mul(f, 32768.0) where 32768.0 = 2^15:
                // for finite nonzero normals, add 15 to exponent;
                // zeros/subnormals remain subnormal-or-zero and behavior
                // maps any exp==0 product to mag=0.
                p_sign = sign;
                if (exp == 8'h00) begin
                    p_exp  = 8'h00;
                    p_frac = 23'd0;
                end else if (exp >= 8'd240) begin
                    p_exp  = 8'hFF;
                    p_frac = 23'd0;
                end else begin
                    p_exp  = exp + 8'd15;
                    p_frac = frac;
                end
                prod = {p_sign, p_exp, p_frac};

                p_sign = prod[31];
                p_exp  = prod[30:23];
                p_frac = prod[22:0];

                if (p_exp == 8'h00) begin
                    mag = 55'd0;
                end else begin
                    e    = p_exp - 127;
                    mant = {1'b1, p_frac};

                    if (e < 0) begin
                        mag = 55'd0;
                    end else if (e >= 31) begin
                        if (p_sign) q15 = -16'sd32768;
                        else        q15 =  16'sd32767;
                        convert_f32_to_q15 = q15[15:0];
                    end else begin
                        if (e >= 23) begin
                            mag = {{31{1'b0}}, mant} << (e - 23);
                        end else begin
                            mag = {{31{1'b0}}, mant} >> (23 - e);
                        end

                        if (p_sign) val = -$signed({1'b0, mag});
                        else        val =  $signed({1'b0, mag});

                        if (val > 56'sd32767) begin
                            q15 = 16'sd32767;
                        end else if (val < -56'sd32768) begin
                            q15 = -16'sd32768;
                        end else begin
                            q15 = val[15:0];
                        end
                    end
                end
            end

            convert_f32_to_q15 = q15[15:0];
        end
    endfunction

    always_ff @(posedge clk) begin
        if (rst) begin
            busy    <= 1'b0;
            out_q15 <= 16'h0000;
        end else if (input_accepted) begin
            out_q15 <= convert_f32_to_q15(in_f32);
            busy    <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule