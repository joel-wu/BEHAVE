module ai_ml_quant_int8_int8_dynamic_quant (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_act_0,
    input  logic [15:0]  in_act_1,
    input  logic [15:0]  in_act_2,
    input  logic [15:0]  in_act_3,
    input  logic [15:0]  in_weight_0,
    input  logic [15:0]  in_weight_1,
    input  logic [15:0]  in_weight_2,
    input  logic [15:0]  in_weight_3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [16:0]  out_act_scale,
    output logic [16:0]  out_weight_scale,
    output logic [7:0]   out_qact_0,
    output logic [7:0]   out_qact_1,
    output logic [7:0]   out_qact_2,
    output logic [7:0]   out_qact_3,
    output logic [7:0]   out_qweight_0,
    output logic [7:0]   out_qweight_1,
    output logic [7:0]   out_qweight_2,
    output logic [7:0]   out_qweight_3
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = !rst && (~busy | output_consumed);
assign out_valid = busy;

function automatic logic [15:0] abs16_u(input logic [15:0] x);
    logic signed [15:0] xs;
    logic [16:0] mag17;
    begin
        xs = x;
        if (xs < 0) begin
            mag17 = -xs;
            abs16_u = mag17[15:0];
        end else begin
            abs16_u = x;
        end
    end
endfunction

function automatic logic [15:0] max2_u16(input logic [15:0] a, input logic [15:0] b);
    begin
        if (a >= b) max2_u16 = a;
        else        max2_u16 = b;
    end
endfunction

function automatic logic [16:0] scale_u17(input logic [15:0] amax);
    logic [31:0] numer;
    logic [31:0] quot;
    begin
        if (amax == 16'd0) begin
            scale_u17 = 16'd0;
        end else begin
            numer = amax * 32'd256 + 32'd126;
            quot = numer / 32'd127;
            scale_u17 = quot[16:0];
        end
    end
endfunction

function automatic logic signed [8:0] quant_one(
    input logic signed [15:0] x,
    input logic [16:0] s_u17
);
    logic signed [24:0] n;
    logic [24:0] abs_n;
    logic [24:0] rounded_mag;
    logic signed [25:0] q0;
    begin
        quant_one = 9'sd0;
        if (s_u17 == 16'd0) begin
            quant_one = 9'sd0;
        end else begin
            n = x * 25'sd256;
            if (n >= 0) begin
                q0 = ($unsigned(n) + {8'd0, (s_u17 >> 1)}) / s_u17;
            end else begin
                abs_n = -n;
                rounded_mag = (abs_n + {8'd0, (s_u17 >> 1)}) / s_u17;
                q0 = -$signed({1'b0, rounded_mag});
            end

            if (q0 > 26'sd127) begin
                quant_one = 9'sd127;
            end else if (q0 < -26'sd127) begin
                quant_one = -9'sd127;
            end else begin
                quant_one = q0[8:0];
            end
        end
    end
endfunction

logic [15:0] act_abs_0, act_abs_1, act_abs_2, act_abs_3;
logic [15:0] weight_abs_0, weight_abs_1, weight_abs_2, weight_abs_3;
logic [15:0] act_max_01, act_max_23, act_amax;
logic [15:0] weight_max_01, weight_max_23, weight_amax;
logic [16:0] act_scale_calc, weight_scale_calc;
logic signed [8:0] qact_s_0, qact_s_1, qact_s_2, qact_s_3;
logic signed [8:0] qweight_s_0, qweight_s_1, qweight_s_2, qweight_s_3;

always_comb begin
    act_abs_0 = abs16_u(in_act_0);
    act_abs_1 = abs16_u(in_act_1);
    act_abs_2 = abs16_u(in_act_2);
    act_abs_3 = abs16_u(in_act_3);

    weight_abs_0 = abs16_u(in_weight_0);
    weight_abs_1 = abs16_u(in_weight_1);
    weight_abs_2 = abs16_u(in_weight_2);
    weight_abs_3 = abs16_u(in_weight_3);

    act_max_01 = max2_u16(act_abs_0, act_abs_1);
    act_max_23 = max2_u16(act_abs_2, act_abs_3);
    act_amax   = max2_u16(act_max_01, act_max_23);

    weight_max_01 = max2_u16(weight_abs_0, weight_abs_1);
    weight_max_23 = max2_u16(weight_abs_2, weight_abs_3);
    weight_amax   = max2_u16(weight_max_01, weight_max_23);

    act_scale_calc    = scale_u17(act_amax);
    weight_scale_calc = scale_u17(weight_amax);

    qact_s_0 = quant_one($signed(in_act_0), act_scale_calc);
    qact_s_1 = quant_one($signed(in_act_1), act_scale_calc);
    qact_s_2 = quant_one($signed(in_act_2), act_scale_calc);
    qact_s_3 = quant_one($signed(in_act_3), act_scale_calc);

    qweight_s_0 = quant_one($signed(in_weight_0), weight_scale_calc);
    qweight_s_1 = quant_one($signed(in_weight_1), weight_scale_calc);
    qweight_s_2 = quant_one($signed(in_weight_2), weight_scale_calc);
    qweight_s_3 = quant_one($signed(in_weight_3), weight_scale_calc);
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy             <= 1'b0;
        out_act_scale    <= 16'h0000;
        out_weight_scale <= 16'h0000;
        out_qact_0       <= 8'h00;
        out_qact_1       <= 8'h00;
        out_qact_2       <= 8'h00;
        out_qact_3       <= 8'h00;
        out_qweight_0    <= 8'h00;
        out_qweight_1    <= 8'h00;
        out_qweight_2    <= 8'h00;
        out_qweight_3    <= 8'h00;
    end else if (input_accepted) begin
        busy             <= 1'b1;
        out_act_scale    <= act_scale_calc;
        out_weight_scale <= weight_scale_calc;
        out_qact_0       <= qact_s_0[7:0];
        out_qact_1       <= qact_s_1[7:0];
        out_qact_2       <= qact_s_2[7:0];
        out_qact_3       <= qact_s_3[7:0];
        out_qweight_0    <= qweight_s_0[7:0];
        out_qweight_1    <= qweight_s_1[7:0];
        out_qweight_2    <= qweight_s_2[7:0];
        out_qweight_3    <= qweight_s_3[7:0];
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
