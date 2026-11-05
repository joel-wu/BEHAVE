module ai_ml_tx_attn_gqa_gqa_grouped_kv_8x #(
    parameter int HEAD_DIM = 16,
    parameter int ELEM_W   = 16
) (
    input  logic                           clk,
    input  logic                           rst,

    input  logic                           in_valid,
    output logic                           in_ready,
    input  logic [HEAD_DIM*ELEM_W-1:0]     in_key,
    input  logic [HEAD_DIM*ELEM_W-1:0]     in_value,

    output logic                           out_valid,
    input  logic                           out_ready,
    output logic [HEAD_DIM*ELEM_W-1:0]     out_key,
    output logic [HEAD_DIM*ELEM_W-1:0]     out_value,
    output logic [2:0]                     out_replica
);

    localparam int unsigned FRAME_BEATS = 8;

    logic [HEAD_DIM*ELEM_W-1:0] key_buf;
    logic [HEAD_DIM*ELEM_W-1:0] value_buf;
    logic [2:0]                 replica_idx;
    logic                       busy;

    wire input_accepted;
    wire output_consumed;
    wire frame_complete;

    assign out_valid       = busy;
    assign out_key         = key_buf;
    assign out_value       = value_buf;
    assign out_replica     = replica_idx;

    assign output_consumed = out_valid & out_ready;
    assign frame_complete  = output_consumed & (replica_idx == 3'd7);
    assign in_ready        = (~busy) | frame_complete;
    assign input_accepted  = in_valid & in_ready;

    always_ff @(posedge clk) begin
        if (rst) begin
            key_buf      <= '0;
            value_buf    <= '0;
            replica_idx  <= 3'd0;
            busy         <= 1'b0;
        end else if (input_accepted) begin
            key_buf      <= in_key;
            value_buf    <= in_value;
            replica_idx  <= 3'd0;
            busy         <= 1'b1;
        end else if (output_consumed) begin
            if (replica_idx == 3'd7) begin
                key_buf      <= key_buf;
                value_buf    <= value_buf;
                replica_idx  <= 3'd0;
                busy         <= 1'b0;
            end else begin
                key_buf      <= key_buf;
                value_buf    <= value_buf;
                replica_idx  <= replica_idx + 3'd1;
                busy         <= 1'b1;
            end
        end
    end

endmodule