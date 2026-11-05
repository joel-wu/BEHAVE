module AiMlSsmMamba2HfMamba2Step (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic signed [31:0] in_gate_0,
    input  logic signed [31:0] in_gate_1,
    input  logic signed [31:0] in_hidden_bc_0,
    input  logic signed [31:0] in_hidden_bc_1,
    input  logic signed [31:0] in_hidden_bc_2,
    input  logic signed [31:0] in_hidden_bc_3,
    input  logic signed [31:0] in_hidden_bc_4,
    input  logic signed [31:0] in_hidden_bc_5,
    input  logic signed [31:0] in_dt_0,

    output logic out_valid,
    input  logic out_ready,
    output logic signed [31:0] out_y_0,
    output logic signed [31:0] out_y_1
);

logic busy;
wire input_accepted;
wire output_consumed;

logic signed [31:0] conv_state [0:5][0:2];
logic signed [31:0] recurrent_state [0:1][0:1];

function automatic logic signed [31:0] qmul(input logic signed [31:0] a, input logic signed [31:0] b);
    logic signed [63:0] prod;
    logic signed [63:0] shifted;
    begin
        prod = a * b;
        shifted = prod >>> 16;
        qmul = shifted[31:0];
    end
endfunction

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


    function automatic logic [63:0] norm_root(input logic [63:0] denominator);
        logic [95:0] target, trial, root;
        begin
            target={denominator,32'd0}; root=0;
            for (int bit_index=47;bit_index>=0;bit_index--) begin
                trial=root | (96'd1 << bit_index);
                if (trial*trial <= target) root=trial;
            end
            norm_root=root[63:0];
        end
    endfunction

    function automatic logic signed [31:0] normalize(input logic signed [31:0] value, input logic [63:0] denominator);
        logic signed [63:0] extended;
        logic [63:0] magnitude, result;
        begin
            extended=$signed({{32{value[31]}},value});
            magnitude=extended<0 ? $unsigned(-extended) : $unsigned(extended);
            result=round_div({32'd0,magnitude} << 32,norm_root(denominator));
            normalize=extended<0 ? -$signed(result[31:0]) : $signed(result[31:0]);
        end
    endfunction

    function automatic logic [63:0] square_word(input logic signed [31:0] value);
        logic signed [63:0] extended;
        begin
            extended=$signed({{32{value[31]}},value});
            square_word=$unsigned(extended*extended);
        end
    endfunction

    function automatic logic signed [31:0] sigmoid16(input logic signed [31:0] value);
        logic signed [63:0] extended;
        logic [63:0] magnitude, tail_value;
        begin
            extended=$signed({{32{value[31]}},value});
            magnitude=extended<0 ? $unsigned(-extended) : $unsigned(extended);
            tail_value=exp40(-$signed(magnitude));
            sigmoid16=32'(round_div(96'd65536*(extended<0 ? {32'd0,tail_value} : {32'd0,Q}),Q+tail_value));
        end
    endfunction
assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

logic signed [31:0] taps0_0, taps0_1, taps0_2;
logic signed [31:0] taps1_0, taps1_1, taps1_2;
logic signed [31:0] taps2_0, taps2_1, taps2_2;
logic signed [31:0] taps3_0, taps3_1, taps3_2;
logic signed [31:0] taps4_0, taps4_1, taps4_2;
logic signed [31:0] taps5_0, taps5_1, taps5_2;

logic signed [31:0] conv_sum_0, conv_sum_1, conv_sum_2, conv_sum_3, conv_sum_4, conv_sum_5;
logic signed [31:0] act_0, act_1, act_2, act_3, act_4, act_5;
logic signed [31:0] hidden_0, hidden_1, b_raw_0, b_raw_1, c_raw_0, c_raw_1;
logic signed [31:0] dt_pre, dt_softplus, dt_times_neg1, d_a_0, d_a_1;
logic signed [31:0] d_b_00, d_b_01, d_b_10, d_b_11;
logic signed [31:0] d_bx_00, d_bx_01, d_bx_10, d_bx_11;
logic signed [31:0] new_state_00, new_state_01, new_state_10, new_state_11;
logic signed [31:0] y_ssm_0, y_ssm_1, y_pre_norm_0, y_pre_norm_1;
logic signed [31:0] gate_act_0, gate_act_1, gated_0, gated_1;
logic [63:0] denominator;
logic signed [31:0] normed_0, normed_1;
logic signed [31:0] calc_out_y_0, calc_out_y_1;

always_comb begin
    taps0_0 = conv_state[0][1];
    taps0_1 = conv_state[0][2];
    taps0_2 = in_hidden_bc_0;
    taps1_0 = conv_state[1][1];
    taps1_1 = conv_state[1][2];
    taps1_2 = in_hidden_bc_1;
    taps2_0 = conv_state[2][1];
    taps2_1 = conv_state[2][2];
    taps2_2 = in_hidden_bc_2;
    taps3_0 = conv_state[3][1];
    taps3_1 = conv_state[3][2];
    taps3_2 = in_hidden_bc_3;
    taps4_0 = conv_state[4][1];
    taps4_1 = conv_state[4][2];
    taps4_2 = in_hidden_bc_4;
    taps5_0 = conv_state[5][1];
    taps5_1 = conv_state[5][2];
    taps5_2 = in_hidden_bc_5;

    conv_sum_0 = qmul(taps0_0, 32'sh0000_4000) + qmul(taps0_1, 32'sh0000_8000) + qmul(taps0_2, 32'sh0001_0000);
    conv_sum_1 = qmul(taps1_0, 32'sh0000_2000) + qmul(taps1_1, 32'sh0000_6000) + qmul(taps1_2, 32'sh0000_C000);
    conv_sum_2 = qmul(taps2_0, 32'sh0000_8000) + qmul(taps2_1, 32'sh0000_4000) + qmul(taps2_2, 32'sh0000_2000);
    conv_sum_3 = qmul(taps3_0, -32'sh0000_4000) + qmul(taps3_1, 32'sh0000_4000) + qmul(taps3_2, 32'sh0000_8000);
    conv_sum_4 = qmul(taps4_0, 32'sh0000_6000) + qmul(taps4_1, 32'sh0000_2000) + qmul(taps4_2, -32'sh0000_2000);
    conv_sum_5 = qmul(taps5_0, 32'sh0000_4000) + qmul(taps5_1, -32'sh0000_4000) + qmul(taps5_2, 32'sh0000_4000);

    act_0 = silu16(conv_sum_0);
    act_1 = silu16(conv_sum_1);
    act_2 = silu16(conv_sum_2);
    act_3 = silu16(conv_sum_3);
    act_4 = silu16(conv_sum_4);
    act_5 = silu16(conv_sum_5);

    hidden_0 = act_0;
    hidden_1 = act_1;
    b_raw_0  = act_2;
    b_raw_1  = act_3;
    c_raw_0  = act_4;
    c_raw_1  = act_5;

    dt_pre        = in_dt_0 + 32'sh0000_8000;
    dt_softplus   = softplus16(dt_pre);
    dt_times_neg1 = qmul(dt_softplus, -32'sh0001_0000);
    d_a_0         = exp16(dt_times_neg1);
    d_a_1         = d_a_0;

    d_b_00 = qmul(dt_softplus, b_raw_0);
    d_b_01 = qmul(dt_softplus, b_raw_1);
    d_b_10 = d_b_00;
    d_b_11 = d_b_01;

    d_bx_00 = qmul(d_b_00, hidden_0);
    d_bx_01 = qmul(d_b_01, hidden_0);
    d_bx_10 = qmul(d_b_10, hidden_1);
    d_bx_11 = qmul(d_b_11, hidden_1);

    new_state_00 = qmul(recurrent_state[0][0], d_a_0) + d_bx_00;
    new_state_01 = qmul(recurrent_state[0][1], d_a_0) + d_bx_01;
    new_state_10 = qmul(recurrent_state[1][0], d_a_1) + d_bx_10;
    new_state_11 = qmul(recurrent_state[1][1], d_a_1) + d_bx_11;

    y_ssm_0 = qmul(new_state_00, c_raw_0) + qmul(new_state_01, c_raw_1);
    y_ssm_1 = qmul(new_state_10, c_raw_0) + qmul(new_state_11, c_raw_1);

    y_pre_norm_0 = y_ssm_0 + qmul(hidden_0, 32'sh0001_0000);
    y_pre_norm_1 = y_ssm_1 + qmul(hidden_1, 32'sh0001_0000);

    gate_act_0 = silu16(in_gate_0);
    gate_act_1 = silu16(in_gate_1);

    gated_0 = qmul(y_pre_norm_0, gate_act_0);
    gated_1 = qmul(y_pre_norm_1, gate_act_1);

    denominator=((square_word(gated_0)+square_word(gated_1)) >> 1)+64'd4295;
    normed_0=normalize(gated_0,denominator);
    normed_1=normalize(gated_1,denominator);

    calc_out_y_0 = qmul(32'sh0001_0000, normed_0);
    calc_out_y_1 = qmul(32'sh0001_0000, normed_1);
end

integer i;
integer j;
always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        out_y_0 <= 32'sd0;
        out_y_1 <= 32'sd0;
        for (i = 0; i < 6; i = i + 1) begin
            for (j = 0; j < 3; j = j + 1) begin
                conv_state[i][j] <= 32'sd0;
            end
        end
        for (i = 0; i < 2; i = i + 1) begin
            for (j = 0; j < 2; j = j + 1) begin
                recurrent_state[i][j] <= 32'sd0;
            end
        end
    end else if (input_accepted) begin
        out_y_0 <= calc_out_y_0;
        out_y_1 <= calc_out_y_1;
        busy <= 1'b1;

        conv_state[0][0] <= taps0_0;
        conv_state[0][1] <= taps0_1;
        conv_state[0][2] <= taps0_2;
        conv_state[1][0] <= taps1_0;
        conv_state[1][1] <= taps1_1;
        conv_state[1][2] <= taps1_2;
        conv_state[2][0] <= taps2_0;
        conv_state[2][1] <= taps2_1;
        conv_state[2][2] <= taps2_2;
        conv_state[3][0] <= taps3_0;
        conv_state[3][1] <= taps3_1;
        conv_state[3][2] <= taps3_2;
        conv_state[4][0] <= taps4_0;
        conv_state[4][1] <= taps4_1;
        conv_state[4][2] <= taps4_2;
        conv_state[5][0] <= taps5_0;
        conv_state[5][1] <= taps5_1;
        conv_state[5][2] <= taps5_2;

        recurrent_state[0][0] <= new_state_00;
        recurrent_state[0][1] <= new_state_01;
        recurrent_state[1][0] <= new_state_10;
        recurrent_state[1][1] <= new_state_11;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
