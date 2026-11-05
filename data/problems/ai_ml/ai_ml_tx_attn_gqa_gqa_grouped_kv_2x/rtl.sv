module ai_ml_tx_attn_gqa_gqa_grouped_kv_2x (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_group_id,
    input  logic [15:0]  in_seq_id,
    input  logic [15:0]  in_kv_head_id,
    input  logic [15:0]  in_head_dim,
    input  logic [255:0] in_key,
    input  logic [255:0] in_value,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_group_id,
    output logic [15:0]  out_seq_id,
    output logic [15:0]  out_attention_head_id,
    output logic [15:0]  out_head_dim,
    output logic [255:0] out_key,
    output logic [255:0] out_value
);

    localparam logic [0:0] FRAME_LAST = 1'b1;

    logic        busy;
    logic        beat_idx;

    logic [15:0] group_id_q;
    logic [15:0] seq_id_q;
    logic [15:0] head_dim_q;
    logic [15:0] head0_q;
    logic [15:0] head1_q;
    logic [255:0] key_q;
    logic [255:0] value_q;

    wire input_accepted;
    wire output_consumed;
    wire frame_complete;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign frame_complete  = output_consumed & (beat_idx == FRAME_LAST);

    assign in_ready  = (~busy) | frame_complete;
    assign out_valid = busy;

    always_comb begin
        out_group_id = 16'h0000;
        out_seq_id = 16'h0000;
        out_attention_head_id = 16'h0000;
        out_head_dim = 16'h0000;
        out_key = 256'h0;
        out_value = 256'h0;

        if (busy) begin
            out_group_id = group_id_q;
            out_seq_id = seq_id_q;
            out_head_dim = head_dim_q;
            out_key = key_q;
            out_value = value_q;

            if (beat_idx == 1'b0) begin
                out_attention_head_id = head0_q;
            end else begin
                out_attention_head_id = head1_q;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            beat_idx <= 1'b0;
            group_id_q <= 16'h0000;
            seq_id_q <= 16'h0000;
            head_dim_q <= 16'h0000;
            head0_q <= 16'h0000;
            head1_q <= 16'h0000;
            key_q <= 256'h0;
            value_q <= 256'h0;
        end else if (input_accepted) begin
            busy <= 1'b1;
            beat_idx <= 1'b0;
            group_id_q <= in_group_id;
            seq_id_q <= in_seq_id;
            head_dim_q <= in_head_dim;
            head0_q <= {in_kv_head_id[14:0], 1'b0};
            head1_q <= {in_kv_head_id[14:0], 1'b0} + 16'h0001;
            key_q <= in_key;
            value_q <= in_value;
        end else if (output_consumed) begin
            if (beat_idx == FRAME_LAST) begin
                busy <= 1'b0;
                beat_idx <= 1'b0;
            end else begin
                busy <= 1'b1;
                beat_idx <= 1'b1;
            end
        end
    end

endmodule