module ai_ml_moe_dispatch_moe_expert_padding #(
    parameter int TOKENS_WIDTH = 32,
    parameter int NUM_EXPERTS_WIDTH = 32,
    parameter int TOP_K_WIDTH = 32,
    parameter int WORLD_SIZE_WIDTH = 32,
    parameter int CAPACITY_FACTOR_NUM_WIDTH = 32,
    parameter int CAPACITY_FACTOR_DEN_WIDTH = 32,
    parameter int CAPACITY_WIDTH = 32
) (
    input  logic                                  clk,
    input  logic                                  rst,

    input  logic                                  in_valid,
    output logic                                  in_ready,
    input  logic [TOKENS_WIDTH-1:0]               in_tokens,
    input  logic [NUM_EXPERTS_WIDTH-1:0]          in_num_experts,
    input  logic [TOP_K_WIDTH-1:0]                in_top_k,
    input  logic [WORLD_SIZE_WIDTH-1:0]           in_world_size,
    input  logic [CAPACITY_FACTOR_NUM_WIDTH-1:0]  in_capacity_factor_num,
    input  logic [CAPACITY_FACTOR_DEN_WIDTH-1:0]  in_capacity_factor_den,

    output logic                                  out_valid,
    input  logic                                  out_ready,
    output logic [CAPACITY_WIDTH-1:0]             out_expert_capacity
);

    localparam int NUM_W = TOKENS_WIDTH + TOP_K_WIDTH + WORLD_SIZE_WIDTH + CAPACITY_FACTOR_NUM_WIDTH;
    localparam int DEN_W = NUM_EXPERTS_WIDTH + CAPACITY_FACTOR_DEN_WIDTH;
    logic busy;
    logic [NUM_W-1:0] numerator, capacity;
    logic [DEN_W-1:0] denominator;
    logic [CAPACITY_WIDTH-1:0] out_expert_capacity_next;
    wire output_consumed = out_valid & out_ready;
    wire input_accepted = in_valid & in_ready;
    assign in_ready = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        numerator = in_top_k * in_tokens;
        numerator = numerator * in_world_size;
        numerator = numerator * in_capacity_factor_num;
        denominator = in_num_experts * in_capacity_factor_den;
        capacity = '0;
        if (denominator != '0) capacity = numerator / denominator;
        out_expert_capacity_next = capacity;
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy                <= 1'b0;
            out_expert_capacity <= '0;
        end else if (input_accepted) begin
            busy                <= 1'b1;
            out_expert_capacity <= out_expert_capacity_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
