module ai_ml_tx_attn_window_mistral_swa_mask (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_query_position,
    input  logic [15:0]  in_key_position,
    input  logic [15:0]  in_sliding_window,

    output logic         out_valid,
    input  logic         out_ready,
    output logic         out_allow
);

logic busy;
logic out_allow_reg;

wire input_accepted;
wire output_consumed;
logic [15:0] distance;
logic        allow_next;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;
assign out_allow = out_allow_reg;

always_comb begin
    distance   = 16'h0000;
    allow_next = 1'b0;

    if (in_key_position <= in_query_position) begin
        distance = in_query_position - in_key_position;
        if (distance < in_sliding_window) begin
            allow_next = 1'b1;
        end
    end
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy          <= 1'b0;
        out_allow_reg <= 1'b0;
    end else if (input_accepted) begin
        busy          <= 1'b1;
        out_allow_reg <= allow_next;
    end else if (output_consumed) begin
        busy          <= 1'b0;
        out_allow_reg <= 1'b0;
    end
end

endmodule