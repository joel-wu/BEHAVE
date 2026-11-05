module ai_ml_ssm_mamba2_mamba2_state_passing (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic in_first_chunk, in_last_chunk, in_has_initstate, in_has_seq_idx,
    input logic [31:0] in_prev_state, in_state_update, in_dA_chunk_cumsum, in_initial_state,
    input logic [31:0] in_prev_seq_idx, in_seq_idx_new,
    output logic out_valid,
    input logic out_ready,
    output logic out_last_chunk,
    output logic [31:0] out_out, out_next_state, out_final_state, out_next_prev_seq_idx
);
    localparam logic [63:0] Q = 64'd1099511627776;
    localparam logic signed [63:0] LN2 = 64'sd762123384786;

    function automatic logic [63:0] round_div(input logic [95:0] n, input logic [63:0] d);
        logic [95:0] quotient, remainder, denominator;
        begin
            denominator = {32'd0,d};
            quotient = n / denominator;
            remainder = n - quotient*denominator;
            if ((remainder << 1) > denominator ||
                ((remainder << 1) == denominator && quotient[0]))
                quotient = quotient + 96'd1;
            round_div = quotient[63:0];
        end
    endfunction

    function automatic logic signed [31:0] qsat(input logic signed [63:0] value);
        begin
            if (value > 64'sd2147483647)
                qsat = 32'sh7fffffff;
            else if (value < -64'sd2147483648)
                qsat = 32'sh80000000;
            else
                qsat = value[31:0];
        end
    endfunction

    function automatic logic signed [31:0] qadd(input logic signed [31:0] a, b);
        begin
            qadd = qsat($signed({{32{a[31]}},a}) + $signed({{32{b[31]}},b}));
        end
    endfunction

    function automatic logic signed [31:0] qmul(input logic signed [31:0] a, b);
        logic signed [63:0] product;
        logic [63:0] magnitude, rounded;
        begin
            product = a*b;
            magnitude = product < 0 ? $unsigned(-product) : $unsigned(product);
            rounded = round_div({32'd0,magnitude},64'd65536);
            qmul = qsat(product < 0 ? -$signed(rounded) : $signed(rounded));
        end
    endfunction

    function automatic logic [63:0] exp40(input logic signed [63:0] value);
        logic signed [63:0] wide;
        integer exponent;
        logic [63:0] residual, term, total;
        begin
            wide = 0;
            exponent = 0;
            residual = 0;
            term = Q;
            total = Q;
            if (value <= -64'sd2097152) begin
                exp40 = 0;
            end else begin
                wide = value * 64'sd16777216;
                exponent = int'(wide / LN2);
                if (wide < 0 && wide % LN2 != 0)
                    exponent = exponent - 1;
                residual = $unsigned(wide - exponent*LN2);
                for (int degree = 1; degree <= 12; degree++) begin
                    term = round_div({32'd0,term} * {32'd0,residual}, Q * 64'(degree));
                    total = total + term;
                end
                if (exponent >= 0)
                    exp40 = total << exponent;
                else
                    exp40 = round_div({32'd0,total},64'd1 << (-exponent));
            end
        end
    endfunction

    function automatic logic signed [31:0] exp16(input logic signed [31:0] value);
        logic [63:0] result40, rounded;
        begin
            result40 = 0;
            rounded = 0;
            if (value >= 32'sd720896)
                exp16 = 32'sh7fffffff;
            else if (value <= -32'sd1048576)
                exp16 = 0;
            else begin
                result40 = exp40($signed({{32{value[31]}},value}));
                rounded = round_div({32'd0,result40},64'd16777216);
                exp16 = qsat($signed(rounded));
            end
        end
    endfunction

    logic signed [31:0] carried, contribution, next_state;
    logic [31:0] previous_index;
    always_comb begin
        carried = $signed(in_prev_state);
        previous_index = in_prev_seq_idx;
        if (in_first_chunk) begin
            carried = in_has_initstate ? $signed(in_initial_state) : 32'sd0;
            previous_index = 0;
        end
        if (in_has_seq_idx && in_seq_idx_new != previous_index)
            contribution = 0;
        else
            contribution = qmul(exp16($signed(in_dA_chunk_cumsum)),carried);
        next_state = qadd(contribution,$signed(in_state_update));
    end
    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 1'b0;
            out_last_chunk <= 1'b0;
            out_out <= '0;
            out_next_state <= '0;
            out_final_state <= '0;
            out_next_prev_seq_idx <= '0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin
                out_last_chunk <= in_last_chunk;
                out_out <= carried;
                out_next_state <= next_state;
                out_final_state <= in_last_chunk ? next_state : 32'd0;
                out_next_prev_seq_idx <= in_has_seq_idx ? in_seq_idx_new : 32'd0;
            end
        end
    end
endmodule
