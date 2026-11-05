module ai_ml_ssm_rwkv_rwkv6_token_shift #(
    parameter int HIDDEN_SIZE = 1,
    parameter int DATA_WIDTH  = 32
) (
    input  logic                               clk,
    input  logic                               rst,

    input  logic                               in_valid,
    output logic                               in_ready,
    input  logic [HIDDEN_SIZE*DATA_WIDTH-1:0]  in_hidden_states,

    output logic                               out_valid,
    input  logic                               out_ready,
    output logic [HIDDEN_SIZE*DATA_WIDTH-1:0]  out_delta
);

    localparam int TOTAL_WIDTH = HIDDEN_SIZE * DATA_WIDTH;

    logic                          busy;
    logic [TOTAL_WIDTH-1:0]        prev_hidden_states;
    logic [TOTAL_WIDTH-1:0]        out_delta_next;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    integer i;
    logic [DATA_WIDTH-1:0] prev_lane;
    logic [DATA_WIDTH-1:0] curr_lane;
    logic [DATA_WIDTH-1:0] delta_lane;

    always_comb begin
        out_delta_next = '0;
        for (i = 0; i < HIDDEN_SIZE; i = i + 1) begin
            prev_lane  = prev_hidden_states[i*DATA_WIDTH +: DATA_WIDTH];
            curr_lane  = in_hidden_states[i*DATA_WIDTH +: DATA_WIDTH];
            delta_lane = prev_lane - curr_lane;
            out_delta_next[i*DATA_WIDTH +: DATA_WIDTH] = delta_lane;
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy               <= 1'b0;
            prev_hidden_states <= '0;
            out_delta          <= '0;
        end else if (input_accepted) begin
            out_delta          <= out_delta_next;
            prev_hidden_states <= in_hidden_states;
            busy               <= 1'b1;
        end else if (output_consumed) begin
            busy     <= 1'b0;
            out_delta <= '0;
        end
    end

endmodule