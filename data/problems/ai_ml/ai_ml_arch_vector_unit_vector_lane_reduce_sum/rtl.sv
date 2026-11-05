module ai_ml_arch_vector_unit_vector_lane_reduce_sum #(
    parameter int LANES = 16,
    parameter int IN_WIDTH = 8,
    parameter int OUT_WIDTH = IN_WIDTH + 4
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic [IN_WIDTH-1:0] in_lane_0,
    input  logic [IN_WIDTH-1:0] in_lane_1,
    input  logic [IN_WIDTH-1:0] in_lane_2,
    input  logic [IN_WIDTH-1:0] in_lane_3,
    input  logic [IN_WIDTH-1:0] in_lane_4,
    input  logic [IN_WIDTH-1:0] in_lane_5,
    input  logic [IN_WIDTH-1:0] in_lane_6,
    input  logic [IN_WIDTH-1:0] in_lane_7,
    input  logic [IN_WIDTH-1:0] in_lane_8,
    input  logic [IN_WIDTH-1:0] in_lane_9,
    input  logic [IN_WIDTH-1:0] in_lane_10,
    input  logic [IN_WIDTH-1:0] in_lane_11,
    input  logic [IN_WIDTH-1:0] in_lane_12,
    input  logic [IN_WIDTH-1:0] in_lane_13,
    input  logic [IN_WIDTH-1:0] in_lane_14,
    input  logic [IN_WIDTH-1:0] in_lane_15,

    output logic out_valid,
    input  logic out_ready,
    output logic [OUT_WIDTH-1:0] out_sum
);

logic busy;

wire input_accepted;
wire output_consumed;

logic [IN_WIDTH:0]     s0, s1, s2, s3, s4, s5, s6, s7;
logic [IN_WIDTH+1:0]   t0, t1, t2, t3;
logic [IN_WIDTH+2:0]   u0, u1;
logic [OUT_WIDTH-1:0]  sum_calc;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    s0 = {1'b0, in_lane_0}  + {1'b0, in_lane_1};
    s1 = {1'b0, in_lane_2}  + {1'b0, in_lane_3};
    s2 = {1'b0, in_lane_4}  + {1'b0, in_lane_5};
    s3 = {1'b0, in_lane_6}  + {1'b0, in_lane_7};
    s4 = {1'b0, in_lane_8}  + {1'b0, in_lane_9};
    s5 = {1'b0, in_lane_10} + {1'b0, in_lane_11};
    s6 = {1'b0, in_lane_12} + {1'b0, in_lane_13};
    s7 = {1'b0, in_lane_14} + {1'b0, in_lane_15};

    t0 = {1'b0, s0} + {1'b0, s1};
    t1 = {1'b0, s2} + {1'b0, s3};
    t2 = {1'b0, s4} + {1'b0, s5};
    t3 = {1'b0, s6} + {1'b0, s7};

    u0 = {1'b0, t0} + {1'b0, t1};
    u1 = {1'b0, t2} + {1'b0, t3};

    sum_calc = {1'b0, u0} + {1'b0, u1};
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy    <= 1'b0;
        out_sum <= '0;
    end else if (input_accepted) begin
        busy    <= 1'b1;
        out_sum <= sum_calc;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule