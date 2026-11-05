module ai_ml_ssm_mamba_mamba_recurrent_step (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_hidden_state,
    input  logic [31:0] in_conv_state_0,
    input  logic [31:0] in_conv_state_1,
    input  logic [31:0] in_conv_state_2,
    input  logic [31:0] in_conv_state_3,
    input  logic [31:0] in_ssm_state_0,
    input  logic [31:0] in_ssm_state_1,
    input  logic [31:0] in_ssm_state_2,
    input  logic [31:0] in_ssm_state_3,
    input  logic [31:0] in_ssm_state_4,
    input  logic [31:0] in_ssm_state_5,
    input  logic [31:0] in_ssm_state_6,
    input  logic [31:0] in_ssm_state_7,
    input  logic [31:0] in_ssm_state_8,
    input  logic [31:0] in_ssm_state_9,
    input  logic [31:0] in_ssm_state_10,
    input  logic [31:0] in_ssm_state_11,
    input  logic [31:0] in_ssm_state_12,
    input  logic [31:0] in_ssm_state_13,
    input  logic [31:0] in_ssm_state_14,
    input  logic [31:0] in_ssm_state_15,

    input  logic [31:0] in_proj_weight_x,
    input  logic [31:0] in_proj_weight_z,
    input  logic [31:0] in_proj_bias_x,
    input  logic [31:0] in_proj_bias_z,

    input  logic [31:0] conv1d_weight_0,
    input  logic [31:0] conv1d_weight_1,
    input  logic [31:0] conv1d_weight_2,
    input  logic [31:0] conv1d_weight_3,
    input  logic [31:0] conv1d_bias,

    input  logic [31:0] x_proj_weight_dt,
    input  logic [31:0] x_proj_weight_B_0,
    input  logic [31:0] x_proj_weight_B_1,
    input  logic [31:0] x_proj_weight_B_2,
    input  logic [31:0] x_proj_weight_B_3,
    input  logic [31:0] x_proj_weight_B_4,
    input  logic [31:0] x_proj_weight_B_5,
    input  logic [31:0] x_proj_weight_B_6,
    input  logic [31:0] x_proj_weight_B_7,
    input  logic [31:0] x_proj_weight_B_8,
    input  logic [31:0] x_proj_weight_B_9,
    input  logic [31:0] x_proj_weight_B_10,
    input  logic [31:0] x_proj_weight_B_11,
    input  logic [31:0] x_proj_weight_B_12,
    input  logic [31:0] x_proj_weight_B_13,
    input  logic [31:0] x_proj_weight_B_14,
    input  logic [31:0] x_proj_weight_B_15,
    input  logic [31:0] x_proj_weight_C_0,
    input  logic [31:0] x_proj_weight_C_1,
    input  logic [31:0] x_proj_weight_C_2,
    input  logic [31:0] x_proj_weight_C_3,
    input  logic [31:0] x_proj_weight_C_4,
    input  logic [31:0] x_proj_weight_C_5,
    input  logic [31:0] x_proj_weight_C_6,
    input  logic [31:0] x_proj_weight_C_7,
    input  logic [31:0] x_proj_weight_C_8,
    input  logic [31:0] x_proj_weight_C_9,
    input  logic [31:0] x_proj_weight_C_10,
    input  logic [31:0] x_proj_weight_C_11,
    input  logic [31:0] x_proj_weight_C_12,
    input  logic [31:0] x_proj_weight_C_13,
    input  logic [31:0] x_proj_weight_C_14,
    input  logic [31:0] x_proj_weight_C_15,

    input  logic [31:0] dt_proj_weight,
    input  logic [31:0] dt_proj_bias,

    input  logic [31:0] A_log_0,
    input  logic [31:0] A_log_1,
    input  logic [31:0] A_log_2,
    input  logic [31:0] A_log_3,
    input  logic [31:0] A_log_4,
    input  logic [31:0] A_log_5,
    input  logic [31:0] A_log_6,
    input  logic [31:0] A_log_7,
    input  logic [31:0] A_log_8,
    input  logic [31:0] A_log_9,
    input  logic [31:0] A_log_10,
    input  logic [31:0] A_log_11,
    input  logic [31:0] A_log_12,
    input  logic [31:0] A_log_13,
    input  logic [31:0] A_log_14,
    input  logic [31:0] A_log_15,

    input  logic [31:0] D_param,
    input  logic [31:0] out_proj_weight,
    input  logic [31:0] out_proj_bias,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_hidden_state,
    output logic [31:0] out_conv_state_0,
    output logic [31:0] out_conv_state_1,
    output logic [31:0] out_conv_state_2,
    output logic [31:0] out_conv_state_3,
    output logic [31:0] out_ssm_state_0,
    output logic [31:0] out_ssm_state_1,
    output logic [31:0] out_ssm_state_2,
    output logic [31:0] out_ssm_state_3,
    output logic [31:0] out_ssm_state_4,
    output logic [31:0] out_ssm_state_5,
    output logic [31:0] out_ssm_state_6,
    output logic [31:0] out_ssm_state_7,
    output logic [31:0] out_ssm_state_8,
    output logic [31:0] out_ssm_state_9,
    output logic [31:0] out_ssm_state_10,
    output logic [31:0] out_ssm_state_11,
    output logic [31:0] out_ssm_state_12,
    output logic [31:0] out_ssm_state_13,
    output logic [31:0] out_ssm_state_14,
    output logic [31:0] out_ssm_state_15
);

    // Handshake state
    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    // Q16.16 multiply: signed 32x32 -> 64, arith shift right 16, take low 32.
    function automatic logic signed [31:0] qmul(input logic signed [31:0] a, input logic signed [31:0] b);
        logic signed [63:0] prod;
        logic signed [63:0] shifted;
        begin
            prod    = a * b;
            shifted = prod >>> 16;
            qmul    = shifted[31:0];
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

    // Combinational compute path
    logic signed [31:0] hidden_s;
    logic signed [31:0] x_pre, z_pre;
    logic signed [31:0] cs0, cs1, cs2, cs3;
    logic signed [31:0] conv_sum, conv_sum_b;
    logic signed [31:0] x_act;
    logic signed [31:0] dt_raw, dt_pre, dt_val;
    logic signed [31:0] B_arr [0:15];
    logic signed [31:0] C_arr [0:15];
    logic signed [31:0] A_arr [0:15];
    logic signed [31:0] dA_arr [0:15];
    logic signed [31:0] dB_arr [0:15];
    logic signed [31:0] ns_arr [0:15];
    logic signed [31:0] y_acc;
    logic signed [31:0] y_skip, y_pre_gate, z_act, y_gated;
    logic signed [31:0] out_h;

    integer ii;

    always_comb begin
        hidden_s = $signed(in_hidden_state);

        // 1. Input projection
        x_pre = qmul(hidden_s, $signed(in_proj_weight_x)) + $signed(in_proj_bias_x);
        z_pre = qmul(hidden_s, $signed(in_proj_weight_z)) + $signed(in_proj_bias_z);

        // 2. Conv shift (insert x_pre at end)
        cs0 = $signed(in_conv_state_1);
        cs1 = $signed(in_conv_state_2);
        cs2 = $signed(in_conv_state_3);
        cs3 = x_pre;

        // 3. Conv sum + bias
        conv_sum = qmul(cs0, $signed(conv1d_weight_0))
                 + qmul(cs1, $signed(conv1d_weight_1))
                 + qmul(cs2, $signed(conv1d_weight_2))
                 + qmul(cs3, $signed(conv1d_weight_3));
        conv_sum_b = conv_sum + $signed(conv1d_bias);

        // 4. SiLU
        x_act = silu16(conv_sum_b);

        // 5. Project to dt, B, C
        dt_raw = qmul(x_act, $signed(x_proj_weight_dt));
        B_arr[0]  = qmul(x_act, $signed(x_proj_weight_B_0));
        B_arr[1]  = qmul(x_act, $signed(x_proj_weight_B_1));
        B_arr[2]  = qmul(x_act, $signed(x_proj_weight_B_2));
        B_arr[3]  = qmul(x_act, $signed(x_proj_weight_B_3));
        B_arr[4]  = qmul(x_act, $signed(x_proj_weight_B_4));
        B_arr[5]  = qmul(x_act, $signed(x_proj_weight_B_5));
        B_arr[6]  = qmul(x_act, $signed(x_proj_weight_B_6));
        B_arr[7]  = qmul(x_act, $signed(x_proj_weight_B_7));
        B_arr[8]  = qmul(x_act, $signed(x_proj_weight_B_8));
        B_arr[9]  = qmul(x_act, $signed(x_proj_weight_B_9));
        B_arr[10] = qmul(x_act, $signed(x_proj_weight_B_10));
        B_arr[11] = qmul(x_act, $signed(x_proj_weight_B_11));
        B_arr[12] = qmul(x_act, $signed(x_proj_weight_B_12));
        B_arr[13] = qmul(x_act, $signed(x_proj_weight_B_13));
        B_arr[14] = qmul(x_act, $signed(x_proj_weight_B_14));
        B_arr[15] = qmul(x_act, $signed(x_proj_weight_B_15));

        C_arr[0]  = qmul(x_act, $signed(x_proj_weight_C_0));
        C_arr[1]  = qmul(x_act, $signed(x_proj_weight_C_1));
        C_arr[2]  = qmul(x_act, $signed(x_proj_weight_C_2));
        C_arr[3]  = qmul(x_act, $signed(x_proj_weight_C_3));
        C_arr[4]  = qmul(x_act, $signed(x_proj_weight_C_4));
        C_arr[5]  = qmul(x_act, $signed(x_proj_weight_C_5));
        C_arr[6]  = qmul(x_act, $signed(x_proj_weight_C_6));
        C_arr[7]  = qmul(x_act, $signed(x_proj_weight_C_7));
        C_arr[8]  = qmul(x_act, $signed(x_proj_weight_C_8));
        C_arr[9]  = qmul(x_act, $signed(x_proj_weight_C_9));
        C_arr[10] = qmul(x_act, $signed(x_proj_weight_C_10));
        C_arr[11] = qmul(x_act, $signed(x_proj_weight_C_11));
        C_arr[12] = qmul(x_act, $signed(x_proj_weight_C_12));
        C_arr[13] = qmul(x_act, $signed(x_proj_weight_C_13));
        C_arr[14] = qmul(x_act, $signed(x_proj_weight_C_14));
        C_arr[15] = qmul(x_act, $signed(x_proj_weight_C_15));

        // 6. dt
        dt_pre = qmul(dt_raw, $signed(dt_proj_weight)) + $signed(dt_proj_bias);
        dt_val = softplus16(dt_pre);

        // 7. A = -exp(A_log)
        A_arr[0]  = -exp16($signed(A_log_0));
        A_arr[1]  = -exp16($signed(A_log_1));
        A_arr[2]  = -exp16($signed(A_log_2));
        A_arr[3]  = -exp16($signed(A_log_3));
        A_arr[4]  = -exp16($signed(A_log_4));
        A_arr[5]  = -exp16($signed(A_log_5));
        A_arr[6]  = -exp16($signed(A_log_6));
        A_arr[7]  = -exp16($signed(A_log_7));
        A_arr[8]  = -exp16($signed(A_log_8));
        A_arr[9]  = -exp16($signed(A_log_9));
        A_arr[10] = -exp16($signed(A_log_10));
        A_arr[11] = -exp16($signed(A_log_11));
        A_arr[12] = -exp16($signed(A_log_12));
        A_arr[13] = -exp16($signed(A_log_13));
        A_arr[14] = -exp16($signed(A_log_14));
        A_arr[15] = -exp16($signed(A_log_15));

        // 8. dA, dB
        for (ii = 0; ii < 16; ii = ii + 1) begin
            dA_arr[ii] = exp16(qmul(dt_val, A_arr[ii]));
            dB_arr[ii] = qmul(dt_val, B_arr[ii]);
        end

        // 9. SSM state update
        ns_arr[0]  = qmul($signed(in_ssm_state_0),  dA_arr[0])  + qmul(x_act, dB_arr[0]);
        ns_arr[1]  = qmul($signed(in_ssm_state_1),  dA_arr[1])  + qmul(x_act, dB_arr[1]);
        ns_arr[2]  = qmul($signed(in_ssm_state_2),  dA_arr[2])  + qmul(x_act, dB_arr[2]);
        ns_arr[3]  = qmul($signed(in_ssm_state_3),  dA_arr[3])  + qmul(x_act, dB_arr[3]);
        ns_arr[4]  = qmul($signed(in_ssm_state_4),  dA_arr[4])  + qmul(x_act, dB_arr[4]);
        ns_arr[5]  = qmul($signed(in_ssm_state_5),  dA_arr[5])  + qmul(x_act, dB_arr[5]);
        ns_arr[6]  = qmul($signed(in_ssm_state_6),  dA_arr[6])  + qmul(x_act, dB_arr[6]);
        ns_arr[7]  = qmul($signed(in_ssm_state_7),  dA_arr[7])  + qmul(x_act, dB_arr[7]);
        ns_arr[8]  = qmul($signed(in_ssm_state_8),  dA_arr[8])  + qmul(x_act, dB_arr[8]);
        ns_arr[9]  = qmul($signed(in_ssm_state_9),  dA_arr[9])  + qmul(x_act, dB_arr[9]);
        ns_arr[10] = qmul($signed(in_ssm_state_10), dA_arr[10]) + qmul(x_act, dB_arr[10]);
        ns_arr[11] = qmul($signed(in_ssm_state_11), dA_arr[11]) + qmul(x_act, dB_arr[11]);
        ns_arr[12] = qmul($signed(in_ssm_state_12), dA_arr[12]) + qmul(x_act, dB_arr[12]);
        ns_arr[13] = qmul($signed(in_ssm_state_13), dA_arr[13]) + qmul(x_act, dB_arr[13]);
        ns_arr[14] = qmul($signed(in_ssm_state_14), dA_arr[14]) + qmul(x_act, dB_arr[14]);
        ns_arr[15] = qmul($signed(in_ssm_state_15), dA_arr[15]) + qmul(x_act, dB_arr[15]);

        // 10. SSM readout (left-fold)
        y_acc = qmul(ns_arr[0], C_arr[0]);
        y_acc = y_acc + qmul(ns_arr[1],  C_arr[1]);
        y_acc = y_acc + qmul(ns_arr[2],  C_arr[2]);
        y_acc = y_acc + qmul(ns_arr[3],  C_arr[3]);
        y_acc = y_acc + qmul(ns_arr[4],  C_arr[4]);
        y_acc = y_acc + qmul(ns_arr[5],  C_arr[5]);
        y_acc = y_acc + qmul(ns_arr[6],  C_arr[6]);
        y_acc = y_acc + qmul(ns_arr[7],  C_arr[7]);
        y_acc = y_acc + qmul(ns_arr[8],  C_arr[8]);
        y_acc = y_acc + qmul(ns_arr[9],  C_arr[9]);
        y_acc = y_acc + qmul(ns_arr[10], C_arr[10]);
        y_acc = y_acc + qmul(ns_arr[11], C_arr[11]);
        y_acc = y_acc + qmul(ns_arr[12], C_arr[12]);
        y_acc = y_acc + qmul(ns_arr[13], C_arr[13]);
        y_acc = y_acc + qmul(ns_arr[14], C_arr[14]);
        y_acc = y_acc + qmul(ns_arr[15], C_arr[15]);

        // 11. Skip
        y_skip = qmul($signed(D_param), x_act);
        y_pre_gate = y_acc + y_skip;

        // 12. SiLU gate
        z_act   = silu16(z_pre);
        y_gated = qmul(y_pre_gate, z_act);

        // 13. Output projection
        out_h = qmul(y_gated, $signed(out_proj_weight)) + $signed(out_proj_bias);
    end

    // Output registers
    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            out_hidden_state <= 32'h0;
            out_conv_state_0 <= 32'h0;
            out_conv_state_1 <= 32'h0;
            out_conv_state_2 <= 32'h0;
            out_conv_state_3 <= 32'h0;
            out_ssm_state_0  <= 32'h0;
            out_ssm_state_1  <= 32'h0;
            out_ssm_state_2  <= 32'h0;
            out_ssm_state_3  <= 32'h0;
            out_ssm_state_4  <= 32'h0;
            out_ssm_state_5  <= 32'h0;
            out_ssm_state_6  <= 32'h0;
            out_ssm_state_7  <= 32'h0;
            out_ssm_state_8  <= 32'h0;
            out_ssm_state_9  <= 32'h0;
            out_ssm_state_10 <= 32'h0;
            out_ssm_state_11 <= 32'h0;
            out_ssm_state_12 <= 32'h0;
            out_ssm_state_13 <= 32'h0;
            out_ssm_state_14 <= 32'h0;
            out_ssm_state_15 <= 32'h0;
        end else if (input_accepted) begin
            busy <= 1'b1;
            out_hidden_state <= out_h;
            out_conv_state_0 <= cs0;
            out_conv_state_1 <= cs1;
            out_conv_state_2 <= cs2;
            out_conv_state_3 <= cs3;
            out_ssm_state_0  <= ns_arr[0];
            out_ssm_state_1  <= ns_arr[1];
            out_ssm_state_2  <= ns_arr[2];
            out_ssm_state_3  <= ns_arr[3];
            out_ssm_state_4  <= ns_arr[4];
            out_ssm_state_5  <= ns_arr[5];
            out_ssm_state_6  <= ns_arr[6];
            out_ssm_state_7  <= ns_arr[7];
            out_ssm_state_8  <= ns_arr[8];
            out_ssm_state_9  <= ns_arr[9];
            out_ssm_state_10 <= ns_arr[10];
            out_ssm_state_11 <= ns_arr[11];
            out_ssm_state_12 <= ns_arr[12];
            out_ssm_state_13 <= ns_arr[13];
            out_ssm_state_14 <= ns_arr[14];
            out_ssm_state_15 <= ns_arr[15];
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
