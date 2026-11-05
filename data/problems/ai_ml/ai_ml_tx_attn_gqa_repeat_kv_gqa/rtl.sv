module ai_ml_tx_attn_gqa_repeat_kv_gqa (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_data,
    input  logic [15:0] in_batch_idx,
    input  logic [15:0] in_kv_head_idx,
    input  logic [15:0] in_seq_idx,
    input  logic [15:0] in_head_dim_idx,
    input  logic [3:0]  in_n_rep,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_data,
    output logic [15:0] out_batch_idx,
    output logic [19:0] out_attn_head_idx,
    output logic [15:0] out_seq_idx,
    output logic [15:0] out_head_dim_idx
);
    logic busy;
    logic [3:0] remaining;
    assign in_ready = !busy;
    assign out_valid = busy;
    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            remaining <= 0;
            out_data <= 0;
            out_batch_idx <= 0;
            out_attn_head_idx <= 0;
            out_seq_idx <= 0;
            out_head_dim_idx <= 0;
        end else if (in_valid && in_ready) begin
            remaining <= in_n_rep;
            busy <= in_n_rep != 0;
            out_data <= in_data;
            out_batch_idx <= in_batch_idx;
            out_attn_head_idx <= 20'(in_kv_head_idx) * 20'(in_n_rep);
            out_seq_idx <= in_seq_idx;
            out_head_dim_idx <= in_head_dim_idx;
        end else if (busy && out_ready) begin
            remaining <= remaining - 4'd1;
            if (remaining == 1) busy <= 0;
            else out_attn_head_idx <= out_attn_head_idx + 20'd1;
        end
    end
endmodule
