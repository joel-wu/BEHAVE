module ai_ml_sparse_2_4_sparse_supermask_2_4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_score0,
    input  logic [31:0] in_score1,
    input  logic [31:0] in_score2,
    input  logic [31:0] in_score3,
    input  logic [31:0] in_weight0,
    input  logic [31:0] in_weight1,
    input  logic [31:0] in_weight2,
    input  logic [31:0] in_weight3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic        out_mask0,
    output logic        out_mask1,
    output logic        out_mask2,
    output logic        out_mask3,
    output logic [31:0] out_weight0,
    output logic [31:0] out_weight1,
    output logic [31:0] out_weight2,
    output logic [31:0] out_weight3
);

logic        busy;
logic [31:0] out_weight0_r;
logic [31:0] out_weight1_r;
logic [31:0] out_weight2_r;
logic [31:0] out_weight3_r;
logic        out_mask0_r;
logic        out_mask1_r;
logic        out_mask2_r;
logic        out_mask3_r;

logic [31:0] s0_c;
logic [31:0] s1_c;
logic [31:0] s2_c;
logic [31:0] s3_c;
logic [31:0] t0_c;
logic [31:0] t1_c;
logic [31:0] t2_c;
logic [31:0] t3_c;
logic [31:0] k_val_c;

logic        mask0_c;
logic        mask1_c;
logic        mask2_c;
logic        mask3_c;
logic [31:0] weight0_c;
logic [31:0] weight1_c;
logic [31:0] weight2_c;
logic [31:0] weight3_c;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

assign out_mask0   = out_mask0_r;
assign out_mask1   = out_mask1_r;
assign out_mask2   = out_mask2_r;
assign out_mask3   = out_mask3_r;
assign out_weight0 = out_weight0_r;
assign out_weight1 = out_weight1_r;
assign out_weight2 = out_weight2_r;
assign out_weight3 = out_weight3_r;

always_comb begin
    s0_c = in_score0;
    s1_c = in_score1;
    s2_c = in_score2;
    s3_c = in_score3;

    t0_c = s0_c;
    t1_c = s1_c;
    t2_c = s2_c;
    t3_c = s3_c;

    if (t0_c > t1_c) begin
        s0_c = t1_c;
        s1_c = t0_c;
    end else begin
        s0_c = t0_c;
        s1_c = t1_c;
    end
    t0_c = s0_c;
    t1_c = s1_c;

    if (t2_c > t3_c) begin
        s2_c = t3_c;
        s3_c = t2_c;
    end else begin
        s2_c = t2_c;
        s3_c = t3_c;
    end
    t2_c = s2_c;
    t3_c = s3_c;

    if (t0_c > t2_c) begin
        s0_c = t2_c;
        s2_c = t0_c;
    end else begin
        s0_c = t0_c;
        s2_c = t2_c;
    end
    t0_c = s0_c;
    t2_c = s2_c;

    if (t1_c > t3_c) begin
        s1_c = t3_c;
        s3_c = t1_c;
    end else begin
        s1_c = t1_c;
        s3_c = t3_c;
    end
    t1_c = s1_c;
    t3_c = s3_c;

    if (t1_c > t2_c) begin
        s1_c = t2_c;
        s2_c = t1_c;
    end else begin
        s1_c = t1_c;
        s2_c = t2_c;
    end

    k_val_c = s2_c;

    mask0_c = (in_score0 < k_val_c) ? 1'b0 : 1'b1;
    mask1_c = (in_score1 < k_val_c) ? 1'b0 : 1'b1;
    mask2_c = (in_score2 < k_val_c) ? 1'b0 : 1'b1;
    mask3_c = (in_score3 < k_val_c) ? 1'b0 : 1'b1;

    weight0_c = mask0_c ? in_weight0 : 32'h00000000;
    weight1_c = mask1_c ? in_weight1 : 32'h00000000;
    weight2_c = mask2_c ? in_weight2 : 32'h00000000;
    weight3_c = mask3_c ? in_weight3 : 32'h00000000;
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy          <= 1'b0;
        out_mask0_r   <= 1'b0;
        out_mask1_r   <= 1'b0;
        out_mask2_r   <= 1'b0;
        out_mask3_r   <= 1'b0;
        out_weight0_r <= 32'h00000000;
        out_weight1_r <= 32'h00000000;
        out_weight2_r <= 32'h00000000;
        out_weight3_r <= 32'h00000000;
    end else if (input_accepted) begin
        busy          <= 1'b1;
        out_mask0_r   <= mask0_c;
        out_mask1_r   <= mask1_c;
        out_mask2_r   <= mask2_c;
        out_mask3_r   <= mask3_c;
        out_weight0_r <= weight0_c;
        out_weight1_r <= weight1_c;
        out_weight2_r <= weight2_c;
        out_weight3_r <= weight3_c;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule