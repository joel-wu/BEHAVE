module ai_ml_ssm_deltanet_qwen3_next_gated_deltanet (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic signed [31:0] in_query,
    input  logic signed [31:0] in_key,
    input  logic signed [31:0] in_value,
    input  logic signed [31:0] in_b,
    input  logic signed [31:0] in_a,
    input  logic signed [31:0] in_z,

    input  logic signed [31:0] in_conv_weight_q_prev,
    input  logic signed [31:0] in_conv_weight_q_cur,
    input  logic signed [31:0] in_conv_weight_k_prev,
    input  logic signed [31:0] in_conv_weight_k_cur,
    input  logic signed [31:0] in_conv_weight_v_prev,
    input  logic signed [31:0] in_conv_weight_v_cur,
    input  logic signed [31:0] in_dt_bias,
    input  logic signed [31:0] in_A,

    output logic out_valid,
    input  logic out_ready,
    output logic signed [31:0] out_data
);

localparam logic signed [31:0] Q16_ONE  = 32'sd65536;
localparam logic signed [31:0] Q16_MONE = -32'sd65536;

logic busy;
wire input_accepted;
wire output_consumed;

logic signed [31:0] conv_state_query;
logic signed [31:0] conv_state_key;
logic signed [31:0] conv_state_value;
logic signed [31:0] recurrent_state;

logic signed [31:0] next_out_data;
logic signed [31:0] next_conv_state_query;
logic signed [31:0] next_conv_state_key;
logic signed [31:0] next_conv_state_value;
logic signed [31:0] next_recurrent_state;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [31:0] q16_mul(input logic signed [31:0] x, input logic signed [31:0] y);
    logic signed [63:0] prod;
    logic signed [63:0] negprod;
    logic signed [63:0] q64;
begin
    prod = x * y;
    negprod = -prod;
    if (prod >= 0)
        q64 = prod >>> 16;
    else
        q64 = -(negprod >>> 16);
    q16_mul = q64[31:0];
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
always_comb begin
    logic signed [31:0] q_conv_acc;
    logic signed [31:0] k_conv_acc;
    logic signed [31:0] v_conv_acc;
    logic signed [31:0] q_conv;
    logic signed [31:0] k_conv;
    logic signed [31:0] v_conv;
    logic signed [31:0] query_scaled;
    logic signed [31:0] key_unit;
    logic signed [31:0] beta;
    logic signed [31:0] dt_in;
    logic signed [31:0] dt;
    logic signed [31:0] g;
    logic signed [31:0] decay;
    logic signed [31:0] kv_mem;
    logic signed [31:0] delta_in;
    logic signed [31:0] delta;
    logic signed [31:0] state_decayed;
    logic signed [31:0] state_add;
    logic signed [31:0] new_recurrent_state_c;
    logic signed [31:0] core_attn;
    logic signed [31:0] normed;
    logic signed [31:0] z_silu;

    q_conv_acc = q16_mul(conv_state_query, in_conv_weight_q_prev) + q16_mul(in_query, in_conv_weight_q_cur);
    k_conv_acc = q16_mul(conv_state_key,   in_conv_weight_k_prev) + q16_mul(in_key,   in_conv_weight_k_cur);
    v_conv_acc = q16_mul(conv_state_value, in_conv_weight_v_prev) + q16_mul(in_value, in_conv_weight_v_cur);

    q_conv = silu16(q_conv_acc);
    k_conv = silu16(k_conv_acc);
    v_conv = silu16(v_conv_acc);

    query_scaled=normalize(q_conv,square_word(q_conv)+64'd4295);
    key_unit=normalize(k_conv,square_word(k_conv)+64'd4295);

    beta = sigmoid16(in_b);
    dt_in = in_a + in_dt_bias;
    dt = softplus16(dt_in);
    g = -q16_mul(in_A, dt);
    decay = exp16(g);

    state_decayed = q16_mul(recurrent_state, decay);
    kv_mem = q16_mul(state_decayed, key_unit);
    delta_in = v_conv - kv_mem;
    delta = q16_mul(delta_in, beta);
    state_add = q16_mul(key_unit, delta);
    new_recurrent_state_c = state_decayed + state_add;

    core_attn = q16_mul(new_recurrent_state_c, query_scaled);

    normed=normalize(core_attn,square_word(core_attn)+64'd4295);

    z_silu = silu16(in_z);

    next_out_data = q16_mul(normed, z_silu);
    next_conv_state_query = in_query;
    next_conv_state_key = in_key;
    next_conv_state_value = in_value;
    next_recurrent_state = new_recurrent_state_c;
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        out_data <= 32'sd0;
        conv_state_query <= 32'sd0;
        conv_state_key <= 32'sd0;
        conv_state_value <= 32'sd0;
        recurrent_state <= 32'sd0;
    end else if (input_accepted) begin
        busy <= 1'b1;
        out_data <= next_out_data;
        conv_state_query <= next_conv_state_query;
        conv_state_key <= next_conv_state_key;
        conv_state_value <= next_conv_state_value;
        recurrent_state <= next_recurrent_state;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
