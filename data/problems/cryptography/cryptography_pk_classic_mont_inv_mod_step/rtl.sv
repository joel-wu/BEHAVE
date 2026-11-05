module cryptography_pk_classic_mont_inv_mod_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_start,
    input  logic [63:0]  in_a,
    input  logic [63:0]  in_modulus,
    input  logic [63:0]  in_accum,
    input  logic [6:0]   in_bit_index,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_accum,
    output logic [6:0]   out_bit_index,
    output logic         out_done
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    logic [63:0] effective_accum_c;
    logic [63:0] exponent_c;
    logic        exp_bit_c;
    logic [63:0] square_c;
    logic [63:0] next_accum_c;
    logic [6:0]  next_bit_index_c;
    logic        next_done_c;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = (~busy) | output_consumed;
    assign out_valid = busy;

    function automatic logic [63:0] inverse64(input logic [63:0] a);
        logic [63:0] x;
        logic [63:0] term64;
        logic [127:0] prod1;
        logic [191:0] prod2;
        begin
            x = (((((a << 1) ^ a) & 64'h0000000000000004) << 1) ^ a);

            prod1 = a * x;
            prod2 = prod1 * x;
            term64 = prod2[63:0];
            x = x + x - term64;

            prod1 = a * x;
            prod2 = prod1 * x;
            term64 = prod2[63:0];
            x = x + x - term64;

            prod1 = a * x;
            prod2 = prod1 * x;
            term64 = prod2[63:0];
            x = x + x - term64;

            prod1 = a * x;
            prod2 = prod1 * x;
            term64 = prod2[63:0];
            x = x + x - term64;

            inverse64 = x;
        end
    endfunction

    function automatic logic [63:0] montgomery_mul(
        input logic [63:0] x,
        input logic [63:0] y,
        input logic [63:0] modulus
    );
        logic [63:0] m0;
        logic [127:0] t;
        logic [63:0] low_t;
        logic [127:0] m_prod;
        logic [63:0] m;
        logic [127:0] mmod;
        logic [128:0] sum;
        logic [64:0] u;
        logic [64:0] u_minus_modulus;
        begin
            if (modulus[0]) begin
                m0 = (~inverse64(modulus)) + 64'd1;
            end else begin
                m0 = 64'd0;
            end

            t = x * y;
            low_t = t[63:0];
            m_prod = low_t * m0;
            m = m_prod[63:0];
            mmod = m * modulus;
            sum = {1'b0, t} + {1'b0, mmod};
            u = sum[128:64];
            u_minus_modulus = u - {1'b0, modulus};

            if (u >= {1'b0, modulus}) begin
                montgomery_mul = u_minus_modulus[63:0];
            end else begin
                montgomery_mul = u[63:0];
            end
        end
    endfunction

    always_comb begin
        effective_accum_c = in_accum;
        if (in_start) effective_accum_c = (in_modulus == 0) ? 64'd0 : (65'h10000000000000000 % in_modulus);
        exponent_c        = in_modulus - 64'd2;

        if (in_bit_index <= 7'd63) begin
            exp_bit_c = ((exponent_c >> in_bit_index) & 64'd1) != 64'd0;
        end else begin
            exp_bit_c = 1'b0;
        end

        square_c = montgomery_mul(effective_accum_c, effective_accum_c, in_modulus);

        if (exp_bit_c) begin
            next_accum_c = montgomery_mul(square_c, in_a, in_modulus);
        end else begin
            next_accum_c = square_c;
        end

        next_bit_index_c = in_bit_index - 7'd1;
        next_done_c      = (in_bit_index == 7'd0);
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy          <= 1'b0;
            out_accum     <= 64'h0000000000000000;
            out_bit_index <= 7'h00;
            out_done      <= 1'b0;
        end else if (input_accepted) begin
            busy          <= 1'b1;
            out_accum     <= next_accum_c;
            out_bit_index <= next_bit_index_c;
            out_done      <= next_done_c;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
