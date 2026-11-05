module ai_ml_ssm_mamba_mamba_s6_dtAB_update (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_state, in_x, in_dt, in_A, in_B, in_C, in_D, in_z, in_dt_bias,
    input logic in_has_D, in_has_z, in_has_dt_bias, in_dt_softplus,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_state, out_out
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

    function automatic logic signed [31:0] softplus16(input logic signed [31:0] value);
        logic signed [63:0] extended;
        logic [63:0] magnitude, tail_value, z, square, term, total, correction;
        begin
            extended = $signed({{32{value[31]}},value});
            magnitude = extended < 0 ? $unsigned(-extended) : $unsigned(extended);
            tail_value = exp40(-$signed(magnitude));
            z = round_div({32'd0,tail_value} * {32'd0,Q}, 2*Q+tail_value);
            square = round_div({32'd0,z} * {32'd0,z}, Q);
            term = z;
            total = z;
            for (int index = 1; index < 12; index++) begin
                term = round_div({32'd0,term} * {32'd0,square}, Q);
                total = total + round_div({32'd0,term},64'(2*index+1));
            end
            correction = round_div({32'd0,total} << 1,64'd16777216);
            softplus16 = qsat((extended > 0 ? extended : 64'sd0) + $signed(correction));
        end
    endfunction

    function automatic logic signed [31:0] silu16(input logic signed [31:0] value);
        logic signed [63:0] extended;
        logic [63:0] magnitude, tail_value, rounded;
        logic [95:0] numerator;
        begin
            extended = $signed({{32{value[31]}},value});
            magnitude = extended < 0 ? $unsigned(-extended) : $unsigned(extended);
            tail_value = exp40(-$signed(magnitude));
            numerator = {32'd0,magnitude} * (extended < 0 ? {32'd0,tail_value} : {32'd0,Q});
            rounded = round_div(numerator,Q+tail_value);
            silu16 = qsat(extended < 0 ? -$signed(rounded) : $signed(rounded));
        end
    endfunction

    logic signed [31:0] dt, decay, drive, next_state, next_output;
    always_comb begin
        dt = $signed(in_dt);
        if (in_has_dt_bias)
            dt = qadd(dt,$signed(in_dt_bias));
        if (in_dt_softplus)
            dt = softplus16(dt);
        decay = exp16(qmul($signed(in_A),dt));
        drive = qmul(qmul($signed(in_B),dt),$signed(in_x));
        next_state = qadd(qmul($signed(in_state),decay),drive);
        next_output = qmul(next_state,$signed(in_C));
        if (in_has_D)
            next_output = qadd(next_output,qmul($signed(in_x),$signed(in_D)));
        if (in_has_z)
            next_output = qmul(next_output,silu16($signed(in_z)));
    end
    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 1'b0;
            out_state <= '0;
            out_out <= '0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin
                out_state <= next_state;
                out_out <= next_output;
            end
        end
    end
endmodule
