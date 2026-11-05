module ai_ml_tx_attn_window_gemma2_alternating_swa (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_layer_idx,

    output logic        out_valid,
    input  logic        out_ready,
    output logic        out_is_sliding_attention
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = (~busy) | output_consumed;
assign out_valid = busy;

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy                     <= 1'b0;
        out_is_sliding_attention <= 1'b0;
    end else if (input_accepted) begin
        busy                     <= 1'b1;
        out_is_sliding_attention <= ~in_layer_idx[0];
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
