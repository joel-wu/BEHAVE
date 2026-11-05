module RadixSortMultipass8b (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_key_0,
    input  logic [31:0] in_key_1,
    input  logic [31:0] in_key_2,
    input  logic [31:0] in_key_3,
    input  logic [31:0] in_key_4,
    input  logic [31:0] in_key_5,
    input  logic [31:0] in_key_6,
    input  logic [31:0] in_key_7,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [2:0]  out_rank_0,
    output logic [2:0]  out_rank_1,
    output logic [2:0]  out_rank_2,
    output logic [2:0]  out_rank_3,
    output logic [2:0]  out_rank_4,
    output logic [2:0]  out_rank_5,
    output logic [2:0]  out_rank_6,
    output logic [2:0]  out_rank_7
);

logic busy;
wire output_consumed;
wire input_accepted;

logic [2:0] sort_idx_0;
logic [2:0] sort_idx_1;
logic [2:0] sort_idx_2;
logic [2:0] sort_idx_3;
logic [2:0] sort_idx_4;
logic [2:0] sort_idx_5;
logic [2:0] sort_idx_6;
logic [2:0] sort_idx_7;

logic [34:0] k0;
logic [34:0] k1;
logic [34:0] k2;
logic [34:0] k3;
logic [34:0] k4;
logic [34:0] k5;
logic [34:0] k6;
logic [34:0] k7;
logic [34:0] t;

assign output_consumed = out_valid & out_ready;
assign input_accepted  = in_valid & in_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    k0 = {in_key_0, 3'd0};
    k1 = {in_key_1, 3'd1};
    k2 = {in_key_2, 3'd2};
    k3 = {in_key_3, 3'd3};
    k4 = {in_key_4, 3'd4};
    k5 = {in_key_5, 3'd5};
    k6 = {in_key_6, 3'd6};
    k7 = {in_key_7, 3'd7};

    if (k0 > k1) begin t = k0; k0 = k1; k1 = t; end
    if (k2 > k3) begin t = k2; k2 = k3; k3 = t; end
    if (k4 > k5) begin t = k4; k4 = k5; k5 = t; end
    if (k6 > k7) begin t = k6; k6 = k7; k7 = t; end

    if (k0 > k2) begin t = k0; k0 = k2; k2 = t; end
    if (k1 > k3) begin t = k1; k1 = k3; k3 = t; end
    if (k4 > k6) begin t = k4; k4 = k6; k6 = t; end
    if (k5 > k7) begin t = k5; k5 = k7; k7 = t; end

    if (k1 > k2) begin t = k1; k1 = k2; k2 = t; end
    if (k5 > k6) begin t = k5; k5 = k6; k6 = t; end

    if (k0 > k4) begin t = k0; k0 = k4; k4 = t; end
    if (k1 > k5) begin t = k1; k1 = k5; k5 = t; end
    if (k2 > k6) begin t = k2; k2 = k6; k6 = t; end
    if (k3 > k7) begin t = k3; k3 = k7; k7 = t; end

    if (k2 > k4) begin t = k2; k2 = k4; k4 = t; end
    if (k3 > k5) begin t = k3; k3 = k5; k5 = t; end

    if (k1 > k2) begin t = k1; k1 = k2; k2 = t; end
    if (k3 > k4) begin t = k3; k3 = k4; k4 = t; end
    if (k5 > k6) begin t = k5; k5 = k6; k6 = t; end

    sort_idx_0 = k0[2:0];
    sort_idx_1 = k1[2:0];
    sort_idx_2 = k2[2:0];
    sort_idx_3 = k3[2:0];
    sort_idx_4 = k4[2:0];
    sort_idx_5 = k5[2:0];
    sort_idx_6 = k6[2:0];
    sort_idx_7 = k7[2:0];
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy       <= 1'b0;
        out_rank_0 <= 3'd0;
        out_rank_1 <= 3'd0;
        out_rank_2 <= 3'd0;
        out_rank_3 <= 3'd0;
        out_rank_4 <= 3'd0;
        out_rank_5 <= 3'd0;
        out_rank_6 <= 3'd0;
        out_rank_7 <= 3'd0;
    end else if (input_accepted) begin
        out_rank_0 <= sort_idx_0;
        out_rank_1 <= sort_idx_1;
        out_rank_2 <= sort_idx_2;
        out_rank_3 <= sort_idx_3;
        out_rank_4 <= sort_idx_4;
        out_rank_5 <= sort_idx_5;
        out_rank_6 <= sort_idx_6;
        out_rank_7 <= sort_idx_7;
        busy       <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule